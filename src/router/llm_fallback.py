"""
src/router/llm_fallback.py
============================
Camada de fallback via LLM do Supervisor (L5) — chamada ao Gemini quando as
camadas rápidas (regex/heurística/KNN) de `supervisor.py` não resolvem.

FUSÃO ROUTER+ORQUESTRADOR (Fase 3, ver notas.md §5.1): este módulo já
abrigou dois classificadores concorrentes (`_classificar_com_flash`, rota de
conteúdo, e um `orchestrate()` que decidia ação de alto nível e SEMPRE
sobrescrevia o Supervisor em `application/runtime/dispatcher.py` — até 2
chamadas LLM pagas por mensagem, brigando por precedência, já causa de 2
incidentes documentados). `orchestrate()` foi absorvido por
`_classificar_com_flash()`, que agora também recebe contexto de memória
(histórico/última tarefa/memória operacional) e classifica
CHECK_STATUS/MEDIA_DOWNLOAD junto com as rotas de conteúdo — um único
classificador, uma única chamada LLM no pior caso.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Classificador de rota/intenção (ex semantic_router._classificar_com_flash)
# ─────────────────────────────────────────────────────────────────────────────

class RoutingDecision(BaseModel):
    """Esquema Pydantic para validação estruturada da decisão de roteamento pelo Gemini."""
    rota: str = Field(description="A rota: CALENDARIO, EDITAL, CONTATOS, WIKI, CRUD, TICKET_ABERTURA, GREETING, SIGAA, MEDIA_DOWNLOAD, CHECK_STATUS, ESCALAR_HUMANO, ou GERAL")
    confianca: float = Field(description="Nível de certeza da decisão (0.0 a 1.0)")
    motivo: str = Field(description="Justificativa breve da decisão (máx 60 caracteres)")


_SYSTEM_ROUTER = """<system_instruction>
Você é um classificador semântico de alta precisão para o Oráculo UEMA.
Sua única responsabilidade é analisar a mensagem de entrada (e o contexto de memória,
se fornecido) e classificá-la em EXATAMENTE uma das rotas válidas.

<rotas_validas>
- CALENDARIO: Dúvidas gerais sobre datas acadêmicas do calendário geral da UEMA, início/fim de aulas, recessos, prazos e matrículas.
- EDITAL: Dúvidas sobre o PAES, editais de vestibular, número de vagas, cotas (AC, BR-PPI, PcD, etc.), documentos exigidos ou isenção de taxa.
- CONTATOS: Pedidos de telefone, e-mail, ramal ou contatos de setores da UEMA (ex: CTIC, PROG, reitoria, secretarias de cursos).
- WIKI: Informações sobre uso do SIGAA (recuperar senha, erro de acesso), rede Wi-Fi, laboratórios ou infraestrutura de sistemas.
- CRUD: Pedidos do usuário para atualizar ou alterar seus próprios dados pessoais de cadastro (ex: "quero mudar meu telefone", "alterar curso", "atualizar meu setor").
- TICKET_ABERTURA: Pedidos para abrir/registrar um chamado ou ticket de suporte técnico (ex: "quero abrir um chamado", "preciso de um ticket", "problema no sistema, preciso de suporte"). Diferente de CRUD: aqui o usuário quer relatar um problema/pedido novo, não alterar seu próprio cadastro.
- GREETING: Saudações puras (ex: "olá", "bom dia"), agradecimentos (ex: "obrigado", "valeu"), ou perguntas sobre sua própria identidade e capacidades (ex: "como você pode me ajudar?", "quem é você?", "o que você faz?").
- SIGAA: Consultas a dados acadêmicos pessoais do discente no SIGAA, incluindo notas, média, histórico escolar, coeficiente de rendimento (CR), índice de rendimento acadêmico (IRA), turmas do semestre, salas de aula, horários, professores, carga horária e estrutura curricular.
- MEDIA_DOWNLOAD: Pedidos para baixar/receber um vídeo, áudio, ou criar um sticker (ex: "manda um vídeo de gatos aí", "quero um sticker dessa foto"), mesmo sem os verbos "buscar"/"baixar" explícitos.
- CHECK_STATUS: Usuário pergunta sobre o andamento/resultado de uma tarefa ou pedido ANTERIOR (ex: "e aí, já saiu?", "como ficou aquilo que pedi?"), ou faz referência direta à "requisição anterior". Só classifique assim se houver contexto de [ÚLTIMA TAREFA]/[MEMÓRIA OPERACIONAL] abaixo indicando que existe algo pendente a checar — sem esse contexto, uma pergunta sobre "status"/"andamento" de algo que não foi mencionado antes é GERAL.
- ESCALAR_HUMANO: Usuário pede explicitamente para falar com uma pessoa/atendente humano, demonstra frustração clara com o bot e quer outro canal de atendimento, ou recusa continuar falando com o robô (ex: "quero falar com um atendente", "me passa pra uma pessoa de verdade", "não quero mais falar com robô"). NÃO classifique assim uma pergunta que só menciona "atendente"/"responsável" de um setor (isso é CONTATOS ou GERAL).
- GERAL: Perguntas fora do escopo oficial da UEMA, conversas informais ou mensagens totalmente ambíguas que não se encaixam em nenhuma outra rota.
</rotas_validas>

<regras_de_classificacao>
1. Se a mensagem for mista contendo uma saudação e uma pergunta factual (ex: "Oi, boa tarde! Qual a data de matrícula?"), desconsidere a saudação e classifique estritamente pela pergunta factual (neste caso, "CALENDARIO").
2. Se o usuário estiver perguntando sobre suas funcionalidades ("o que você pode fazer?", "me ajuda"), classifique como "GREETING" para que ele receba a resposta de apresentação.
3. Se houver bloco [ÚLTIMA TAREFA] e a pergunta atual for claramente sobre o resultado dela, classifique como "CHECK_STATUS" em vez da rota de conteúdo que a palavra-chave sugeriria isoladamente.
4. Se houver bloco [MEMÓRIA OPERACIONAL] e o usuário estiver só reagindo a uma informação prévia (confirmando, agradecendo), considere "GREETING" ou "CHECK_STATUS" conforme o caso.
5. Responda estritamente com o JSON estruturado conforme o esquema Pydantic, sem formatações adicionais ou blocos markdown.
</regras_de_classificacao>
</system_instruction>"""


async def _classificar_com_flash(query: str, ctx: dict, session_id: str | None = None):
    """Usa o provider LLM ativo (Gemini/DeepSeek/Groq, ver llm_factory.py)
    para classificação zero-shot da rota/intenção — absorve o antigo
    orchestrate() (Fase 3, ver notas.md §5.1): quando há session_id, também
    injeta contexto de memória (histórico/última tarefa/memória operacional)
    no prompt, o suficiente pra decidir CHECK_STATUS sem precisar de uma
    segunda chamada LLM separada."""
    from src.router.contracts import RouterDecision, ROTAS_VALIDAS
    from src.infrastructure.adapters.llm_factory import get_llm_provider

    ctx_str = f"Aluno de {ctx['curso']}" if ctx.get("curso") else ""

    ctx_parts = [ctx_str] if ctx_str else []
    if session_id:
        try:
            from src.memory.services.redis_memory_service import get_cognitive_memory
            mem = get_cognitive_memory()
            history_summary = await mem.format_history(session_id)
            task_history = await mem.get_task_history(session_id)
            operational_memory = await mem.get_operational(session_id)
        except Exception:
            history_summary, task_history, operational_memory = "", {}, {}

        if history_summary:
            ctx_parts.append(f"[HISTÓRICO RECENTE]\n{history_summary[-800:]}")
        if task_history and task_history.get("last_worker"):
            ctx_parts.append(
                f"[ÚLTIMA TAREFA]\nWorker: {task_history['last_worker']}\n"
                f"Resultado: {task_history.get('last_result', '')[:200]}\n"
                f"(DICA: Se a pergunta for sobre o resultado acima, classifique como 'CHECK_STATUS')"
            )
        if operational_memory and operational_memory.get("last_action"):
            ctx_parts.append(
                f"[MEMÓRIA OPERACIONAL]\nÚltima ação: {operational_memory['last_action']}\n"
                f"Se o usuário estiver apenas reagindo a uma informação prévia, considere 'GREETING' ou 'CHECK_STATUS'."
            )

    prompt = "\n\n".join(ctx_parts + [f"Mensagem: \"{query[:300]}\"\nClassifique:"])

    try:
        provider = get_llm_provider(agente="router", rota="router_classify")
        decision_validated = await provider.gerar_resposta_estruturada_async(
            prompt=prompt,
            response_schema=RoutingDecision,
            system_instruction=_SYSTEM_ROUTER,
            temperatura=0.0,
            user_id=session_id or "",
            rota="router_classify",
        )
        if decision_validated is None:
            from src.infrastructure.observability.metrics import PrometheusMetrics
            PrometheusMetrics().increment_orchestrator_json_parse_failure()
            raise RuntimeError("provider não devolveu decisão válida")

        if session_id:
            tokens_in, tokens_out = provider.ultimo_uso_tokens
            # Mantido em paralelo à telemetria persistente (llm_factory.py) —
            # é o cache curto que o simulador de avaliação do /hub consome.
            from src.infrastructure.redis_client import registrar_tokens_redis
            registrar_tokens_redis(session_id, tokens_in, tokens_out)

        rota = decision_validated.rota.upper()
        if rota not in ROTAS_VALIDAS:
            # Também aceita se existir no Redis config (intents semeadas dinamicamente)
            from src.infrastructure.redis_client import get_redis_text
            r_text = get_redis_text()
            if not r_text.hexists("router:config", rota):
                rota = "GERAL"

        confianca = float(decision_validated.confianca)
        motivo = str(decision_validated.motivo)[:60]

        logger.info("🧭 [ROUTER] Flash: rota=%s conf=%.2f | '%.40s'", rota, confianca, query)

        from src.router.supervisor import _dag_hint_para_rota
        return RouterDecision(
            rota=rota, confianca=confianca, motivo=motivo,
            cache_hit=False, cache_layer="miss", latencia_ms=0,
            dag_hint=_dag_hint_para_rota(rota, query),
        )

    except Exception as e:
        logger.error("❌ [ROUTER] Flash falhou, usando fallback regex: %s", e)
        from src.router.supervisor import _dag_hint_para_rota
        rota = _regex_fallback(query)
        return RouterDecision(
            rota=rota, confianca=0.4, motivo=f"regex_fallback: {type(e).__name__}",
            cache_hit=False, cache_layer="miss", latencia_ms=0,
            dag_hint=_dag_hint_para_rota(rota, query),
        )


def _regex_fallback(query: str) -> str:
    """Fallback de último recurso quando Flash falha."""
    q = query.lower()
    if re.search(r"matr[íi]cula|calend|prazo|semestre|trancamento|aula", q):
        return "CALENDARIO"
    if re.search(r"paes|vestibular|vaga|cota|inscri|edital", q):
        return "EDITAL"
    if re.search(r"email|telefone|contato|ctic\b|prog\b|reitoria", q):
        return "CONTATOS"
    if re.search(r"sigaa|senha|wifi|sistema|suporte|laborat", q):
        return "WIKI"
    return "GERAL"
