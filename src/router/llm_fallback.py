"""
src/router/llm_fallback.py
============================
Camadas de fallback via LLM do Supervisor — chamadas ao Gemini quando as
camadas rápidas (regex/heurística/KNN) de `supervisor.py` não resolvem.

Reúne dois classificadores que hoje coexistem no fluxo (chamados em sequência
por application/chain/cognitive_os.py, decomposto na Fase 3):

  1. classificar_com_flash(): classifica a ROTA/intenção da mensagem
     (ex-`application/routing/semantic_router.py::_classificar_com_flash`).
  2. orchestrate(): decide a AÇÃO de alto nível para linguagem natural
     (ex-`application/routing/llm_orchestrator.py`, hoje chamado
     "terceiro cérebro" por rodar em paralelo ao classificador de rota).

NOTA DE ESCOPO (Fase 2): os dois fallbacks foram apenas RELOCADOS para este
módulo, preservando comportamento e assinatura idênticos (a fusão física em
um único arquivo já elimina a duplicação de "onde mexer" quando se quer
trocar de modelo/parâmetros). A fusão *comportamental* das duas chamadas
(hoje cognitive_os.py invoca as duas em sequência para toda mensagem que não
é comando) é lógica de orquestração de `cognitive_os.py` e será tratada na
Fase 3, quando esse arquivo for decomposto — não faz sentido arriscar mudança
de comportamento aqui só para "ter um cérebro só" sem re-testar o fluxo
completo de HITL/memória que depende da ordem atual das duas chamadas.
"""
from __future__ import annotations

import json
import logging
import re

from prometheus_client import Counter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FLASH_TOKENS = Counter(
    "oraculo_router_gemini_flash_tokens_total",
    "Tokens consumidos pelo Gemini Flash no router",
    ["direction"],
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Classificador de rota/intenção (ex semantic_router._classificar_com_flash)
# ─────────────────────────────────────────────────────────────────────────────

class RoutingDecision(BaseModel):
    """Esquema Pydantic para validação estruturada da decisão de roteamento pelo Gemini."""
    rota: str = Field(description="A rota: CALENDARIO, EDITAL, CONTATOS, WIKI, CRUD, GREETING, SIGAA, ou GERAL")
    confianca: float = Field(description="Nível de certeza da decisão (0.0 a 1.0)")
    motivo: str = Field(description="Justificativa breve da decisão (máx 60 caracteres)")


_SYSTEM_ROUTER = """<system_instruction>
Você é um classificador semântico de alta precisão para o Oráculo UEMA.
Sua única responsabilidade é analisar a mensagem de entrada e classificá-la em EXATAMENTE uma das rotas válidas.

<rotas_validas>
- CALENDARIO: Dúvidas gerais sobre datas acadêmicas do calendário geral da UEMA, início/fim de aulas, recessos, prazos e matrículas.
- EDITAL: Dúvidas sobre o PAES, editais de vestibular, número de vagas, cotas (AC, BR-PPI, PcD, etc.), documentos exigidos ou isenção de taxa.
- CONTATOS: Pedidos de telefone, e-mail, ramal ou contatos de setores da UEMA (ex: CTIC, PROG, reitoria, secretarias de cursos).
- WIKI: Informações sobre uso do SIGAA (recuperar senha, erro de acesso), rede Wi-Fi, laboratórios ou infraestrutura de sistemas.
- CRUD: Pedidos do usuário para atualizar ou alterar seus próprios dados pessoais de cadastro (ex: "quero mudar meu telefone", "alterar curso").
- GREETING: Saudações puras (ex: "olá", "bom dia"), agradecimentos (ex: "obrigado", "valeu"), ou perguntas sobre sua própria identidade e capacidades (ex: "como você pode me ajudar?", "quem é você?", "o que você faz?").
- SIGAA: Consultas a dados acadêmicos pessoais do discente no SIGAA, incluindo notas, média, histórico escolar, coeficiente de rendimento (CR), índice de rendimento acadêmico (IRA), turmas do semestre, salas de aula, horários, professores, carga horária e estrutura curricular.
- GERAL: Perguntas fora do escopo oficial da UEMA, conversas informais ou mensagens totalmente ambíguas que não se encaixam em nenhuma outra rota.
</rotas_validas>

<regras_de_classificacao>
1. Se a mensagem for mista contendo uma saudação e uma pergunta factual (ex: "Oi, boa tarde! Qual a data de matrícula?"), desconsidere a saudação e classifique estritamente pela pergunta factual (neste caso, "CALENDARIO").
2. Se o usuário estiver perguntando sobre suas funcionalidades ("o que você pode fazer?", "me ajuda"), classifique como "GREETING" para que ele receba a resposta de apresentação.
3. Responda estritamente com o JSON estruturado conforme o esquema Pydantic, sem formatações adicionais ou blocos markdown.
</regras_de_classificacao>
</system_instruction>"""


async def _classificar_com_flash(query: str, ctx: dict, session_id: str | None = None):
    """Usa Gemini Flash para classificação zero-shot da rota/intenção."""
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types
    from src.router.contracts import RouterDecision, ROTAS_VALIDAS

    ctx_str = f"Aluno de {ctx['curso']}" if ctx.get("curso") else ""
    prompt = f"{ctx_str}\nMensagem: \"{query[:300]}\"\nClassifique:"

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_ROUTER,
                temperature=0.0,
                max_output_tokens=150,
                response_mime_type="application/json",
                response_schema=RoutingDecision,
            ),
        )

        # Métricas de tokens
        usage = response.usage_metadata
        if usage:
            _FLASH_TOKENS.labels(direction="input").inc(usage.prompt_token_count or 0)
            _FLASH_TOKENS.labels(direction="output").inc(usage.candidates_token_count or 0)
            if session_id:
                from src.infrastructure.redis_client import registrar_tokens_redis
                registrar_tokens_redis(session_id, usage.prompt_token_count or 0, usage.candidates_token_count or 0)

        # Parsing seguro do JSON
        texto = response.text.strip()
        if texto.startswith("```json"):
            texto = texto[7:-3].strip()
        elif texto.startswith("```"):
            texto = texto[3:-3].strip()

        data = json.loads(texto or "{}")

        # Validação via Pydantic schema
        decision_validated = RoutingDecision(**data)

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


# ─────────────────────────────────────────────────────────────────────────────
# 2. Classificador de ação de alto nível (ex llm_orchestrator.py)
# ─────────────────────────────────────────────────────────────────────────────

ACTIONS = ["reply_direct", "call_rag", "call_sigaa", "check_status", "call_media"]


class OrchestratorDecision(BaseModel):
    action: str = Field(description=f"Uma de: {ACTIONS}")
    reasoning: str = Field(description="Motivo da decisão em até 60 chars")
    route_hint: str = Field(default="GERAL",
        description="Sub-rota opcional: CALENDARIO, EDITAL, CONTATOS, WIKI, GERAL")


_SYSTEM = """Você é o orquestrador do Oráculo UEMA.
Analise a mensagem e decida a ação correta:

- reply_direct: saudação, agradecimento, pergunta sobre você mesmo
- call_rag: dúvida sobre documentos, calendário, editais, contatos, wiki
- call_sigaa: notas, histórico, turmas, CR, IRA, estrutura curricular
- check_status: usuário pergunta sobre andamento, solicita o resultado de uma tarefa/requisição anterior, ou faz referência à 'requisição anterior'.
- call_media: usuário pede para baixar um vídeo, áudio, criar sticker, ou processar mídia

Você é uma API. RETORNE EXCLUSIVAMENTE UM JSON VÁLIDO obedecendo ao schema exigido. NÃO RETORNE MARKDOWN, NEM TEXTO EXPLICATIVO."""

_client = None
def _get_client():
    global _client
    if _client is None:
        from src.infrastructure.settings import settings
        import google.genai as genai
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


async def orchestrate(
    message: str,
    history_summary: str = "",
    task_history: dict | None = None,
    operational_memory: dict | None = None,
    user_context: dict | None = None,
    session_id: str = "",
) -> OrchestratorDecision:
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types

    ctx_parts = []
    if history_summary:
        ctx_parts.append(f"[HISTÓRICO RECENTE]\n{history_summary[-800:]}")
    if task_history and task_history.get("last_worker"):
        ctx_parts.append(
            f"[ÚLTIMA TAREFA]\nWorker: {task_history['last_worker']}\n"
            f"Resultado: {task_history.get('last_result', '')[:200]}\n"
            f"(DICA: Se a pergunta for sobre o resultado acima, retorne a ação 'check_status')"
        )
    if operational_memory and operational_memory.get("last_action"):
        ctx_parts.append(
            f"[MEMÓRIA OPERACIONAL]\nÚltima ação: {operational_memory['last_action']}\n"
            f"Se o usuário estiver apenas reagindo a uma informação prévia, considere 'reply_direct' ou 'check_status'."
        )

    prompt = "\n\n".join(ctx_parts + [f"Mensagem: \"{message[:300]}\""])

    try:
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                temperature=0.0,
                max_output_tokens=120,
                response_mime_type="application/json",
                response_schema=OrchestratorDecision,
            ),
        )

        usage = response.usage_metadata
        if usage and session_id:
            from src.infrastructure.redis_client import registrar_tokens_redis
            registrar_tokens_redis(session_id,
                                   usage.prompt_token_count or 0,
                                   usage.candidates_token_count or 0)

        # 1. Extrair e limpar o texto (Remove blocos markdown ```json ... ```)
        raw_text = response.text or "{}"
        clean_text = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.IGNORECASE|re.MULTILINE)

        # 2. Parse seguro
        data = json.loads(clean_text)
        decision = OrchestratorDecision(**data)

        if decision.action not in ACTIONS:
            decision.action = "call_rag"

        logger.info("🎯 [ORCHESTRATOR] action=%s route=%s | '%s'",
                    decision.action, decision.route_hint, message[:40])
        return decision

    except json.JSONDecodeError as e:
        logger.error(f"❌ [ORCHESTRATOR] JSON Inválido: '{raw_text}' | Erro: {e}")
        return OrchestratorDecision(action="call_rag", reasoning="fallback_json", route_hint="GERAL")
    except Exception as e:
        logger.warning("⚠️  [ORCHESTRATOR] falhou, fallback call_rag: %s", e)
        return OrchestratorDecision(action="call_rag", reasoning="fallback", route_hint="GERAL")
