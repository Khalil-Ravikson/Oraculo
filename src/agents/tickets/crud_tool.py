"""
src/agents/tickets/crud_tool.py
==================================
CRUD como *tool* dentro do agente `tickets` (item 3 da rodada de testes) —
não é um agente novo. Máquina de estados simples, mesma ESTRUTURA de
`agents/conversation/registration.py::RegistrationFunnel` (perguntas em
sequência, uma por vez), mas escopada a uma única operação de exemplo:
atualizar o próprio setor (centro) e/ou telefone — só para validar o padrão
tool + RBAC sem tocar o banco de verdade nesta rodada.

Chave Redis própria `crud_update_draft:{session_id}` (redis_state.py).
Gravação final respeita `settings.DEV_TEST_NO_DB_WRITE` (mesmo mecanismo de
`registration_repository.py` — nenhuma lógica nova, só o mesmo `if`).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.application.runtime.dispatcher import OSResult
from src.capabilities.persistence import redis_state

logger = logging.getLogger(__name__)

_RE_SIM = re.compile(r"^(sim|s|confirmo|ok|certo|correto)\s*[!.]?$", re.I)
_RE_NAO = re.compile(r"^(n[ãa]o|n|cancela|cancelar)\s*[!.]?$", re.I)
_RE_CANCELAMENTO = re.compile(r"(esque[cç]e|cancela|deixa\s+pra\s+l[áa]|muda\s+de\s+assunto|desist)", re.I)

_PERGUNTA_CAMPO = (
    "O que você quer atualizar: seu *setor* ou seu *telefone*? "
    "(responda 'setor' ou 'telefone')"
)


async def start_crud_update(
    decision, message: str, session_id: str, user_context: dict, r: Any, t0: float
) -> OSResult:
    from src.agents.tickets.rbac import checar_permissao_chamado
    autorizado, msg_bloqueio, _ = await checar_permissao_chamado(session_id)
    ms = int((time.monotonic() - t0) * 1000)
    if not autorizado:
        return OSResult(answer=msg_bloqueio, plan_id="crud_rbac_blocked", rota="CRUD",
                         cache_hit=False, total_ms=ms, status="ok")

    draft = {"step": "ask_field", "campo": None, "valor": None}
    await redis_state.set_crud_draft(r, session_id, draft)
    return OSResult(answer=_PERGUNTA_CAMPO, plan_id=f"crud_start_{int(time.time())}", rota="CRUD",
                     cache_hit=False, total_ms=ms, status="hitl_pending")


async def handle_crud_continuation(message: str, session_id: str, user_context: dict, r: Any) -> OSResult | None:
    draft = await redis_state.get_crud_draft(r, session_id)
    if draft is None:
        return None

    msg = message.strip()
    if _RE_CANCELAMENTO.search(msg):
        await redis_state.delete_crud_draft(r, session_id)
        return None

    step = draft.get("step", "ask_field")

    if step == "ask_field":
        campo_lower = msg.lower()
        if "setor" in campo_lower or "centro" in campo_lower:
            draft["campo"] = "centro"
        elif "telefone" in campo_lower or "celular" in campo_lower:
            draft["campo"] = "telefone"
        else:
            return OSResult(answer=f"Não entendi. {_PERGUNTA_CAMPO}", plan_id="crud_reprompt",
                             rota="CRUD", cache_hit=False, total_ms=5, status="hitl_pending")
        draft["step"] = "ask_value"
        pergunta = "Qual é o novo setor/centro?" if draft["campo"] == "centro" else "Qual é o novo telefone?"
        await redis_state.set_crud_draft(r, session_id, draft)
        return OSResult(answer=pergunta, plan_id="crud_ask_value", rota="CRUD",
                         cache_hit=False, total_ms=5, status="hitl_pending")

    if step == "ask_value":
        if len(msg) < 2:
            pergunta = "Qual é o novo setor/centro?" if draft["campo"] == "centro" else "Qual é o novo telefone?"
            return OSResult(answer=pergunta, plan_id="crud_reprompt", rota="CRUD",
                             cache_hit=False, total_ms=5, status="hitl_pending")
        draft["valor"] = msg
        draft["step"] = "ask_confirmation"
        campo_label = "setor" if draft["campo"] == "centro" else "telefone"
        await redis_state.set_crud_draft(r, session_id, draft)
        return OSResult(
            answer=f"Confirma atualizar seu *{campo_label}* para *{msg}*? (responda *sim* ou *não*)",
            plan_id="crud_confirmation", rota="CRUD", cache_hit=False, total_ms=5, status="hitl_pending",
        )

    if step == "ask_confirmation":
        if _RE_SIM.match(msg):
            return await _finalizar(draft, session_id, r)
        if _RE_NAO.match(msg):
            await redis_state.delete_crud_draft(r, session_id)
            return OSResult(answer="❌ Atualização cancelada.", plan_id="crud_cancelado", rota="CRUD",
                             cache_hit=False, total_ms=5, status="ok")
        return OSResult(answer="Responda *sim* para confirmar ou *não* para cancelar.",
                         plan_id="crud_reprompt", rota="CRUD", cache_hit=False, total_ms=5, status="hitl_pending")

    await redis_state.delete_crud_draft(r, session_id)
    return None


async def _finalizar(draft: dict, session_id: str, r: Any) -> OSResult:
    from src.agents.tickets.rbac import checar_permissao_chamado
    autorizado, msg_bloqueio, _ = await checar_permissao_chamado(session_id)
    if not autorizado:
        await redis_state.delete_crud_draft(r, session_id)
        return OSResult(answer=msg_bloqueio, plan_id="crud_rbac_blocked", rota="CRUD",
                         cache_hit=False, total_ms=5, status="ok")

    from src.infrastructure.settings import settings

    campo, valor = draft["campo"], draft["valor"]
    if settings.DEV_TEST_NO_DB_WRITE:
        from src.capabilities.persistence.dev_dump import salvar_json_dev
        caminho = salvar_json_dev("crud_dev", session_id, {
            "telefone_atual": session_id, "campo": campo, "novo_valor": valor,
        })
        logger.info("🔧 [CRUD-DEV] Atualização simulada salva em %s", caminho)
    else:
        from src.capabilities.persistence.ticket_repository import atualizar_setor_e_telefone
        if campo == "centro":
            await atualizar_setor_e_telefone(session_id, novo_centro=valor)
        else:
            await atualizar_setor_e_telefone(session_id, novo_telefone=valor)

    await redis_state.delete_crud_draft(r, session_id)
    campo_label = "setor" if campo == "centro" else "telefone"
    disclaimer = "\n\n🧪 _Ambiente de TESTE: gravado localmente, não alterou o banco de verdade._" \
        if settings.DEV_TEST_NO_DB_WRITE else ""
    return OSResult(
        answer=f"✅ Seu {campo_label} foi atualizado para *{valor}*.{disclaimer}",
        plan_id="crud_finalizado", rota="CRUD", cache_hit=False, total_ms=10, status="ok",
    )
