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

    # Como o HITL já foi processado de forma instantânea (Fast-Path),
    # aqui nós apenas focamos em realizar o download pesado!
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
    if not result.ok:
        payload["answer"] = f"❌ Falha no download do YouTube: {result.error}"
    else:
        payload["answer"] = f"✅ Download concluído!\n🎬 **{result.title}**\n📁 Salvo em: {result.file_path}"
        logger.info("📥 [YTB] '%s' → %s (%.1fMB)",
                    result.title[:40] if result.title else "?", result.file_path, result.file_size_mb)
        
    _salvar(event["plan_id"], event["step_id"], payload)
    
    # Publicar resposta final diretamente para o Stream!
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()
    r.xadd(
        "oraculo:stream:final_responses",
        {
            "plan_id": event["plan_id"],
            "session_id": event["session_id"],
            "status": "ok" if result.ok else "error",
            "answer": payload["answer"],
            "latency_ms": "10",
            "ts": "0"
        },
        maxlen=2000, approximate=True
    )

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
    if not result.ok:
        payload["answer"] = f"❌ Falha no download do Instagram: {result.error}"
    else:
        payload["answer"] = f"✅ Download concluído!\n📸 **{result.title}**\n📁 Salvo em: {result.file_path}"
        logger.info("📸 [INSTA] → %s (%.1fMB)", result.file_path, result.file_size_mb)

    _salvar(event["plan_id"], event["step_id"], payload)
    
    # Publicar resposta final diretamente para o Stream!
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()
    r.xadd(
        "oraculo:stream:final_responses",
        {
            "plan_id": event["plan_id"],
            "session_id": event["session_id"],
            "status": "ok" if result.ok else "error",
            "answer": payload["answer"],
            "latency_ms": "10",
            "ts": "0"
        },
        maxlen=2000, approximate=True
    )
    
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