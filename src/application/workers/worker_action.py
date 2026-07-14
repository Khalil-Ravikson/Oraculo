"""
src/application/workers/worker_action.py
===========================================
Worker Celery de ações administrativas — emagrecido na Fase 6 do
PLANO_REFATORACAO_SUPERVISOR.md (seção 2.6): só desempacota o evento e
delega a decisão para `agents/tickets/service.py::TicketService`. SQL cru
vive em `capabilities/persistence/ticket_repository.py`.

Ver docstring de `agents/tickets/service.py` para o achado desta fase: hoje
nada no roteamento vivo despacha `dispatch("action", ...)` — este worker
está registrado no Celery/WorkerRegistry mas dormente. Mantido chamável
exatamente como antes (mesmo nome de task, mesmas actions), só reorganizado.
"""
from __future__ import annotations
import asyncio, logging

from src.agents.tickets.service import TicketService
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)

_service = TicketService()

_HANDLERS = {
    "update_student_email": lambda args: _service.atualizar_email(args.get("matricula", ""), args.get("novo_email", "")),
    "abrir_chamado_glpi":    lambda args: _service.abrir_chamado_glpi(args.get("titulo", "Chamado sem título"), args.get("user_id", "")),
    "enviar_email":          lambda args: _service.enviar_email(args.get("destinatario", ""), args.get("assunto", ""), args.get("corpo", "")),
}


@register("action")
@celery_app.task(name="worker_action", bind=True, max_retries=2, queue="default")
def worker_action_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {plan_id, session_id, step_id, action: str, args: dict}
    """
    action  = event.get("action", "")
    args    = event.get("args", {})
    plan_id = event.get("plan_id", "")
    step_id = event.get("step_id", "s_action")

    handler = _HANDLERS.get(action)
    if not handler:
        payload = {"status": "error", "error": f"Action '{action}' não registrada."}
        _salvar(plan_id, step_id, payload)
        return payload

    try:
        result = await handler(args)
        payload = {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("❌ [ACTION] '%s' falhou: %s", action, e)
        payload = {"status": "error", "error": str(e)[:200]}

    _salvar(plan_id, step_id, payload)
    return payload


def _salvar(plan_id, step_id, data):
    import json
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 120,
            json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        pass
