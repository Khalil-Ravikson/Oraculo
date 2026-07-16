from __future__ import annotations
import asyncio, logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("audio_to_text")
@celery_app.task(name="worker_audio_to_text", bind=True, max_retries=2, queue="media")
def worker_audio_to_text_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {
      plan_id, session_id, step_id,
      audio_b64: str,       # base64 do áudio
      mime_type: str,       # audio/ogg | audio/mp4 | audio/wav
      plan_context: dict
    }
    """
    import base64
    from src.infrastructure.services.audio_service import get_audio_service
    from src.application.workers.worker_rag_search import _publicar

    plan_id  = event.get("plan_id", "")
    step_id  = event.get("step_id", "s_stt")
    b64      = event.get("audio_b64", "")
    mime     = event.get("mime_type", "audio/ogg")

    if not b64:
        _publicar_resultado(plan_id, event["session_id"], step_id,
                            {"error": "audio_b64 vazio", "status": "error"})
        return {"status": "error"}

    audio_bytes = base64.b64decode(b64)
    svc    = get_audio_service()
    result = await svc.transcribe(audio_bytes, mime)

    payload = {
        "transcription": result.text,
        "status":        "ok" if result.ok else "error",
        "error":         result.error,
    }
    _publicar_resultado(plan_id, event["session_id"], step_id, payload)

    logger.info("🎤 [STT] plan=%s | texto='%.60s'", plan_id[:8], result.text)
    return payload


def _publicar_resultado(plan_id, session_id, step_id, data):
    import json
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.setex(f"plan:results:{plan_id}:{step_id}", 120,
                json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.warning("⚠️  [STT] publicar falhou: %s", e)