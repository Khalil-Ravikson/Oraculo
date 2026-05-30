from __future__ import annotations
import asyncio, logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("ytb_download")
@celery_app.task(name="worker_ytb_download", bind=True, max_retries=1, queue="media")
def worker_ytb_download_task(self, event: dict) -> dict:
    return asyncio.run(_ytb(event))


@register("insta_download")
@celery_app.task(name="worker_insta_download", bind=True, max_retries=1, queue="media")
def worker_insta_download_task(self, event: dict) -> dict:
    return asyncio.run(_insta(event))


async def _ytb(event: dict) -> dict:
    """
    event: {plan_id, session_id, step_id, url: str, audio_only: bool = False}
    """
    from src.infrastructure.services.media_download_service import get_media_service
    svc    = get_media_service()
    result = await svc.download_youtube(
        event["url"],
        audio_only=event.get("audio_only", False)
    )
    payload = {
        "status":       "ok" if result.ok else "error",
        "file_path":    result.file_path,
        "title":        result.title,
        "duration_s":   result.duration_s,
        "file_size_mb": result.file_size_mb,
        "media_type":   result.media_type,
        "error":        result.error,
    }
    _salvar(event["plan_id"], event["step_id"], payload)
    logger.info("📥 [YTB] '%s' → %s (%.1fMB)",
                result.title[:40], result.file_path, result.file_size_mb)
    return payload


async def _insta(event: dict) -> dict:
    """
    event: {plan_id, session_id, step_id, url: str}
    """
    from src.infrastructure.services.media_download_service import get_media_service
    svc    = get_media_service()
    result = await svc.download_instagram(event["url"])
    payload = {
        "status":       "ok" if result.ok else "error",
        "file_path":    result.file_path,
        "title":        result.title,
        "file_size_mb": result.file_size_mb,
        "media_type":   result.media_type,
        "error":        result.error,
    }
    _salvar(event["plan_id"], event["step_id"], payload)
    logger.info("📸 [INSTA] → %s (%.1fMB)", result.file_path, result.file_size_mb)
    return payload


def _salvar(plan_id, step_id, data):
    import json
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 300,
            json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        pass