from __future__ import annotations
import asyncio, base64, logging, os
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("text_to_audio")
@celery_app.task(name="worker_text_to_audio", bind=True, max_retries=2, queue="media")
def worker_text_to_audio_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {plan_id, session_id, step_id, text: str, lang: str = "pt"}
    Retorna audio_b64 + file_path temporário.
    """
    from src.infrastructure.services.audio_service import get_audio_service

    plan_id = event.get("plan_id", "")
    step_id = event.get("step_id", "s_tts")
    text    = event.get("text", "")
    lang    = event.get("lang", "pt")

    svc    = get_audio_service()
    result = await svc.synthesize(text, lang)

    payload: dict = {"status": "ok" if result.ok else "error", "error": result.error}

    if result.ok and os.path.exists(result.audio_path):
        with open(result.audio_path, "rb") as f:
            payload["audio_b64"] = base64.b64encode(f.read()).decode()
        payload["audio_path"] = result.audio_path
        # Arquivo em /tmp — quem consumir é responsável por deletar

    _salvar(plan_id, step_id, payload)
    logger.info("🔊 [TTS] plan=%s | text='%.40s'", plan_id[:8], text)
    return payload


def _salvar(plan_id, step_id, data):
    import json
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 120,
            json.dumps({k: v for k, v in data.items() if k != "audio_b64"},
                       ensure_ascii=False)
        )
    except Exception:
        pass