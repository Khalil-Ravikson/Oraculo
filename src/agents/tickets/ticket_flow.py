"""
src/agents/tickets/ticket_flow.py
====================================
Funil de abertura de chamado, ambiente de TESTE ponta-a-ponta via WhatsApp
(ver notas_regras_negocio_chunkviz.md — decisão de escopo 2026-07-16, item
"tickets" reaberto agora só para essa rodada de testes). Mesmo padrão de
`agents/sigaa/auth_flow.py`: state machine em Redis, funções livres chamadas
pelo `dispatcher.py`, sem LangGraph/StateGraph.

Chave própria `ticket_draft:{session_id}` (TTL 18min, ver
`capabilities/persistence/redis_state.py`) — não colide com `hitl:session:*`
do SIGAA.

Não existe API GLPI real ainda: ao confirmar, o payload coletado é gravado em
`dados/tmp/tickets_dev/{session_id}_{timestamp}.json` (capabilities/persistence/dev_dump.py)
para inspeção manual — nenhuma chamada HTTP externa é feita. A mensagem de
sucesso é um template fixo (não gerado por LLM) justamente para nunca alucinar
um número de chamado real — ver item 5 da rodada de testes.

CPF: coletado "ao vivo" só para o checklist da descrição, e NUNCA é escrito
no draft Redis nem no JSON final (mesmo cuidado do CPF no fluxo SIGAA,
agents/sigaa/auth_flow.py, que também não persiste CPF/senha em disco).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from src.application.runtime.dispatcher import OSResult
from src.capabilities.persistence import redis_state

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Seed local de categorias ITIL (não existe API do GLPI ainda — ver item 2b)
# ─────────────────────────────────────────────────────────────────────────────
SEED_CATEGORIAS: list[dict] = [
    {"id": 1, "nome": "Rede e Conectividade (Wi-Fi, cabo, VPN)"},
    {"id": 2, "nome": "Hardware (computador, periférico, impressora)"},
    {"id": 3, "nome": "Software e Sistemas (SIGAA, e-mail institucional)"},
    {"id": 4, "nome": "Acesso e Conta (senha, permissão, login)"},
    {"id": 5, "nome": "Telefonia"},
    {"id": 6, "nome": "Infraestrutura predial (elétrica, mobiliário)"},
    {"id": 7, "nome": "Outros"},
]
_CATEGORIA_POR_ID = {c["id"]: c["nome"] for c in SEED_CATEGORIAS}


class _InferenciaTicket(BaseModel):
    tipo: str = Field(description="'Incidente' (algo quebrou/parou) ou 'Requisicao' (pedido novo) ou 'Ambiguo'")
    tipo_confianca: float = Field(description="0.0 a 1.0")
    categoria_id: int = Field(description="ID da categoria mais provável da lista fornecida, ou 0 se ambíguo")
    categoria_confianca: float = Field(description="0.0 a 1.0")


class _TituloTicket(BaseModel):
    titulo: str = Field(description="Título curto (máx 80 caracteres) resumindo o problema/pedido")


_SYSTEM_INFERENCIA = f"""Você classifica pedidos de chamado de suporte técnico da UEMA.
Categorias disponíveis (responda com o ID numérico):
{chr(10).join(f"{c['id']}. {c['nome']}" for c in SEED_CATEGORIAS)}

Regras:
- "tipo": Incidente = algo que estava funcionando e parou/quebrou. Requisicao = um pedido novo (ex: instalar programa, criar acesso).
  Se não der pra saber com confiança >= 0.7, retorne "Ambiguo".
- "categoria_id": se não houver confiança >= 0.7 em nenhuma categoria, retorne 0.
Retorne só o JSON do schema, sem texto extra."""

_STEP_ORDER = [
    "ask_type", "ask_category", "ask_location", "ask_missing_prefill",
    "ask_cpf", "ask_tombamento", "ask_queixa", "ask_attachment", "ask_confirmation",
]

_CAMPOS_PREFILL = [
    ("nome", "Qual é o seu nome completo?"),
    ("email", "Qual é o seu e-mail de contato?"),
    ("telefone", "Qual é o seu telefone de contato?"),
    ("role", "Qual é o seu vínculo com a UEMA (aluno, servidor, professor)?"),
    ("centro", "Qual é o seu setor/centro na UEMA?"),
]

_PERGUNTAS_FIXAS = {
    "ask_location": "Em qual prédio/setor você está agora?",
    "ask_cpf": "Para o checklist do chamado, informe seu *CPF* (só números). Ele é usado só nesta conversa — não fica salvo em nenhum lugar.",
    "ask_tombamento": "Se souber, informe o número de *tombamento* do equipamento (ou responda 'não' se não tiver/não se aplica).",
    "ask_queixa": "Agora me conte com suas palavras: qual é o *problema ou pedido* que você quer registrar?",
    "ask_attachment": "Quer anexar algum arquivo (print do erro, comprovante etc. — até 2MB)? Envie o arquivo agora ou responda 'não'.",
}

_RE_CANCELAMENTO = re.compile(
    r"(esque[cç]e|cancela|deixa\s+pra\s+l[áa]|muda\s+de\s+assunto|desist|para\s+(com\s+)?isso)",
    re.I,
)
_RE_FORA_DE_ASSUNTO = re.compile(
    r"(card[áa]pio|calend[áa]rio|edital|paes|vestibular|sigaa\b|\bcr\b|\bira\b|hist[óo]rico\s+escolar|"
    r"quem\s+(é|e)\s+voc[êe]|o\s+que\s+voc[êe]\s+(faz|pode))",
    re.I,
)
_RE_SIM = re.compile(r"^(sim|s|confirmo|ok|certo|correto)\s*[!.]?$", re.I)
_RE_NAO = re.compile(r"^(n[ãa]o|n|cancela|cancelar)\s*[!.]?$", re.I)
_RE_PULAR = re.compile(r"^(n[ãa]o|n|pular|skip|n[ãa]o\s+tenho|nenhum)\s*[!.]?$", re.I)

_DISCLAIMER_TESTE = (
    "\n\n🧪 _Ambiente de TESTE: nenhum chamado foi enviado ao GLPI de verdade. "
    "Os dados foram salvos localmente só para conferência._"
)


# ─────────────────────────────────────────────────────────────────────────────
# Início do funil
# ─────────────────────────────────────────────────────────────────────────────

async def start_ticket_abertura(
    decision, message: str, session_id: str, user_context: dict, r: Any, t0: float
) -> OSResult:
    from src.agents.tickets.rbac import checar_permissao_chamado
    autorizado, msg_bloqueio, pessoa = await checar_permissao_chamado(session_id)
    ms = int((time.monotonic() - t0) * 1000)
    if not autorizado:
        return OSResult(
            answer=msg_bloqueio, plan_id="ticket_rbac_blocked", rota="TICKET_ABERTURA",
            cache_hit=False, total_ms=ms, status="ok",
        )

    inferencia = await _inferir_tipo_categoria(message)

    data: dict = {
        "nome": pessoa.get("nome"),
        "email": pessoa.get("email"),
        "telefone": pessoa.get("telefone") or session_id,
        "role": pessoa.get("role"),
        "centro": pessoa.get("centro"),
        "curso": pessoa.get("curso"),
        "matricula": pessoa.get("matricula"),
    }
    if inferencia and inferencia.tipo_confianca >= 0.7 and inferencia.tipo in ("Incidente", "Requisicao"):
        data["tipo"] = inferencia.tipo
    if inferencia and inferencia.categoria_confianca >= 0.7 and inferencia.categoria_id in _CATEGORIA_POR_ID:
        data["itilcategories_id"] = inferencia.categoria_id
        data["itilcategories_nome"] = _CATEGORIA_POR_ID[inferencia.categoria_id]

    campos_faltando = [chave for chave, _ in _CAMPOS_PREFILL if not data.get(chave)]

    draft = {"step": "__start__", "data": data, "campos_faltando": campos_faltando}
    pergunta = _proxima_etapa(draft)
    await redis_state.set_ticket_draft(r, session_id, draft)

    return OSResult(
        answer=f"📋 Vamos abrir seu chamado de teste!\n\n{pergunta}",
        plan_id=f"ticket_start_{int(time.time())}",
        rota="TICKET_ABERTURA",
        cache_hit=False,
        total_ms=ms,
        status="hitl_pending",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Continuação (chamado a cada mensagem, ANTES do roteamento normal)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_ticket_continuation(message: str, session_id: str, user_context: dict, r: Any) -> OSResult | None:
    draft = await redis_state.get_ticket_draft(r, session_id)
    if draft is None:
        if await redis_state.ticket_draft_expirou(r, session_id):
            await redis_state.delete_ticket_draft(r, session_id)
            return OSResult(
                answer=(
                    "⌛ Seu rascunho de chamado expirou por inatividade (ficou parado por "
                    "muito tempo). Se ainda precisar, me diga novamente que você quer abrir um chamado."
                ),
                plan_id="ticket_draft_expired", rota="TICKET_ABERTURA",
                cache_hit=False, total_ms=5, status="ok",
            )
        return None

    step = draft.get("step", "ask_confirmation")
    msg_clean = message.strip()

    if _RE_CANCELAMENTO.search(msg_clean):
        await redis_state.delete_ticket_draft(r, session_id)
        return None  # deixa o roteamento normal responder à mensagem (ex: nova pergunta)

    if step not in ("ask_location", "ask_missing_prefill", "ask_tombamento", "ask_queixa",
                    "ask_attachment", "ask_confirmation"):
        if _RE_FORA_DE_ASSUNTO.search(msg_clean):
            await redis_state.delete_ticket_draft(r, session_id)
            return None

    try:
        return await _avancar_step(step, msg_clean, draft, session_id, user_context, r)
    except Exception as e:
        logger.error("Erro no funil de tickets (step=%s): %s", step, e)
        return None


async def _avancar_step(step: str, msg: str, draft: dict, session_id: str, user_context: dict, r: Any) -> OSResult:
    data = draft["data"]

    if step == "ask_type":
        tipo = _parse_tipo(msg)
        if not tipo:
            return _reperguntar(draft, "Não entendi. Responda *1* para Incidente ou *2* para Requisição.")
        data["tipo"] = tipo

    elif step == "ask_category":
        cat = _parse_categoria(msg)
        if not cat:
            return _reperguntar(draft, "Não entendi a categoria. Responda com o número da lista.")
        data["itilcategories_id"], data["itilcategories_nome"] = cat

    elif step == "ask_location":
        if len(msg) < 2:
            return _reperguntar(draft, "Pode informar o prédio/setor onde você está?")
        data["locations_texto"] = msg

    elif step == "ask_missing_prefill":
        campo = draft["campos_faltando"][0]
        if len(msg) < 2:
            return _reperguntar(draft, dict(_CAMPOS_PREFILL)[campo])
        data[campo] = msg
        draft["campos_faltando"].pop(0)
        if draft["campos_faltando"]:
            proximo = draft["campos_faltando"][0]
            pergunta = dict(_CAMPOS_PREFILL)[proximo]
            await redis_state.set_ticket_draft(r, session_id, draft)
            return OSResult(answer=pergunta, plan_id="ticket_prefill", rota="TICKET_ABERTURA",
                             cache_hit=False, total_ms=5, status="hitl_pending")

    elif step == "ask_cpf":
        cpf = re.sub(r"\D", "", msg)
        if len(cpf) != 11:
            return _reperguntar(draft, "❌ CPF inválido — precisa ter 11 dígitos numéricos. Informe novamente:")
        # CPF NUNCA entra em `data`/draft — usado só para validar o formato ao vivo.

    elif step == "ask_tombamento":
        if not _RE_PULAR.match(msg):
            data["tombamento"] = msg

    elif step == "ask_queixa":
        if len(msg) < 5:
            return _reperguntar(draft, "Pode descrever um pouco melhor o problema ou pedido?")
        data["queixa"] = msg
        data["titulo"] = await _gerar_titulo(msg)

    elif step == "ask_attachment":
        if _RE_PULAR.match(msg):
            data["anexo"] = None
        elif user_context.get("has_media"):
            data["anexo"] = {"media_type": user_context.get("media_type", ""), "validado": True,
                              "obs": "tamanho não verificável neste ambiente de teste"}
        else:
            return _reperguntar(draft, "Envie o arquivo agora ou responda 'não' para seguir sem anexo.")

    elif step == "ask_confirmation":
        if _RE_SIM.match(msg):
            return await _finalizar(draft, session_id, r)
        if _RE_NAO.match(msg):
            await redis_state.delete_ticket_draft(r, session_id)
            return OSResult(answer="❌ Rascunho de chamado cancelado.", plan_id="ticket_cancelado",
                             rota="TICKET_ABERTURA", cache_hit=False, total_ms=5, status="ok")
        return _reperguntar(draft, "Responda *sim* para confirmar o envio ou *não* para cancelar.")

    pergunta = _proxima_etapa(draft)
    await redis_state.set_ticket_draft(r, session_id, draft)
    return OSResult(answer=pergunta, plan_id="ticket_continua", rota="TICKET_ABERTURA",
                     cache_hit=False, total_ms=5, status="hitl_pending")


def _reperguntar(draft: dict, texto: str) -> OSResult:
    return OSResult(answer=texto, plan_id="ticket_reprompt", rota="TICKET_ABERTURA",
                     cache_hit=False, total_ms=5, status="hitl_pending")


async def _finalizar(draft: dict, session_id: str, r: Any) -> OSResult:
    from src.agents.tickets.rbac import checar_permissao_chamado
    autorizado, msg_bloqueio, _ = await checar_permissao_chamado(session_id)
    if not autorizado:
        await redis_state.delete_ticket_draft(r, session_id)
        return OSResult(answer=msg_bloqueio, plan_id="ticket_rbac_blocked", rota="TICKET_ABERTURA",
                         cache_hit=False, total_ms=5, status="ok")

    from src.capabilities.persistence.dev_dump import salvar_json_dev

    payload = dict(draft["data"])
    caminho = salvar_json_dev("tickets_dev", session_id, payload)
    logger.info("📋 [TICKET-DEV] Rascunho salvo em %s", caminho)

    await redis_state.delete_ticket_draft(r, session_id)

    resumo = _montar_resumo(payload)
    return OSResult(
        answer=f"✅ Chamado de teste registrado!\n\n{resumo}{_DISCLAIMER_TESTE}",
        plan_id="ticket_finalizado", rota="TICKET_ABERTURA", cache_hit=False, total_ms=10, status="ok",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de fluxo/parse
# ─────────────────────────────────────────────────────────────────────────────

def _proxima_etapa(draft: dict) -> str:
    data = draft["data"]
    idx = _STEP_ORDER.index(draft["step"]) if draft.get("step") in _STEP_ORDER else -1

    for nome in _STEP_ORDER[idx + 1:]:
        if nome == "ask_type" and data.get("tipo"):
            continue
        if nome == "ask_category" and data.get("itilcategories_id"):
            continue
        if nome == "ask_missing_prefill":
            if draft["campos_faltando"]:
                draft["step"] = nome
                return dict(_CAMPOS_PREFILL)[draft["campos_faltando"][0]]
            continue
        if nome == "ask_confirmation":
            draft["step"] = nome
            return _montar_resumo(data) + "\n\n*Confirma o envio?* (responda *sim* ou *não*)"

        draft["step"] = nome
        if nome == "ask_type":
            return "É um *Incidente* (algo parou de funcionar) ou uma *Requisição* (um pedido novo)? Responda *1* ou *2*."
        if nome == "ask_category":
            lista = "\n".join(f"{c['id']}. {c['nome']}" for c in SEED_CATEGORIAS)
            return f"Qual categoria melhor descreve o problema?\n{lista}"
        return _PERGUNTAS_FIXAS[nome]

    draft["step"] = "ask_confirmation"
    return _montar_resumo(data) + "\n\n*Confirma o envio?* (responda *sim* ou *não*)"


def _parse_tipo(msg: str) -> str | None:
    m = msg.strip().lower()
    if m in ("1",) or "incidente" in m:
        return "Incidente"
    if m in ("2",) or "requisi" in m:
        return "Requisicao"
    return None


def _parse_categoria(msg: str) -> tuple[int, str] | None:
    m = msg.strip()
    if m.isdigit() and int(m) in _CATEGORIA_POR_ID:
        cid = int(m)
        return cid, _CATEGORIA_POR_ID[cid]
    m_lower = m.lower()
    for c in SEED_CATEGORIAS:
        if c["nome"].lower().split(" (")[0] in m_lower:
            return c["id"], c["nome"]
    return None


def _montar_resumo(data: dict) -> str:
    linhas = [
        f"*Tipo:* {data.get('tipo', '—')}",
        f"*Categoria:* {data.get('itilcategories_nome', '—')}",
        f"*Local:* {data.get('locations_texto', '—')}",
        f"*Nome:* {data.get('nome', '—')}",
        f"*E-mail:* {data.get('email', '—')}",
        f"*Telefone:* {data.get('telefone', '—')}",
        f"*Vínculo:* {data.get('role', '—')}",
        f"*Setor:* {data.get('centro', '—')}",
        f"*Tombamento:* {data.get('tombamento', 'não informado')}",
        f"*Título:* {data.get('titulo', '—')}",
        f"*Descrição:* {data.get('queixa', '—')}",
        f"*Anexo:* {'sim' if data.get('anexo') else 'não'}",
    ]
    return "\n".join(linhas)


# ─────────────────────────────────────────────────────────────────────────────
# Chamadas LLM (Gemini)
# ─────────────────────────────────────────────────────────────────────────────

async def _inferir_tipo_categoria(message: str) -> _InferenciaTicket | None:
    from src.infrastructure.adapters.gemini_provider import get_gemini_provider
    provider = get_gemini_provider()
    try:
        return await provider.gerar_resposta_estruturada_async(
            prompt=f"Mensagem do usuário: \"{message[:500]}\"",
            response_schema=_InferenciaTicket,
            system_instruction=_SYSTEM_INFERENCIA,
        )
    except Exception as e:
        logger.warning("⚠️ Inferência de tipo/categoria do ticket falhou: %s", e)
        return None


async def _gerar_titulo(queixa: str) -> str:
    from src.infrastructure.adapters.gemini_provider import get_gemini_provider
    provider = get_gemini_provider()
    try:
        resultado = await provider.gerar_resposta_estruturada_async(
            prompt=f"Descrição do usuário: \"{queixa[:500]}\"",
            response_schema=_TituloTicket,
            system_instruction="Resuma a descrição a seguir em um título curto (máx 80 caracteres) para um chamado de suporte técnico. Só o título, sem pontuação final.",
        )
        if resultado and resultado.titulo:
            return resultado.titulo[:80]
    except Exception as e:
        logger.warning("⚠️ Geração de título do ticket falhou: %s", e)
    return queixa[:80]
