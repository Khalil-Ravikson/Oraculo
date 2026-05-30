from __future__ import annotations
import asyncio, logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)

_HANDLERS: dict = {}


def action_handler(name: str):
    """Decorator para registrar handlers de ações."""
    def decorator(fn):
        _HANDLERS[name] = fn
        return fn
    return decorator


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


# ── Handlers concretos ────────────────────────────────────────────────────────

@action_handler("update_student_email")
async def _update_email(args: dict) -> dict:
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text
    matricula = args.get("matricula", "")
    novo_email = args.get("novo_email", "")
    if not matricula or not novo_email:
        raise ValueError("matricula e novo_email obrigatórios")
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE pessoas SET email=:e WHERE matricula=:m"),
            {"e": novo_email, "m": matricula}
        )
        await db.commit()
    return {"mensagem": f"✅ E-mail atualizado para {novo_email}"}


@action_handler("abrir_chamado_glpi")
async def _abrir_chamado(args: dict) -> dict:
    # Integre com GLPI real via HTTP quando disponível
    titulo  = args.get("titulo", "Chamado sem título")
    user_id = args.get("user_id", "")
    logger.info("📋 [GLPI] Chamado: '%s' | user=%s", titulo, user_id)
    return {"mensagem": f"✅ Chamado '{titulo}' registrado. Acompanhe pelo GLPI."}


@action_handler("enviar_email")
async def _enviar_email(args: dict) -> dict:
    dest    = args.get("destinatario", "")
    assunto = args.get("assunto", "")
    corpo   = args.get("corpo", "")
    if not dest:
        raise ValueError("destinatario obrigatório")
    try:
        from src.infrastructure.services.domain_service.gmail_service import get_gmail_service
        svc    = get_gmail_service()
        result = await svc.send(dest, assunto, corpo)
        return {"mensagem": result}
    except Exception as e:
        raise RuntimeError(f"Email falhou: {e}")


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