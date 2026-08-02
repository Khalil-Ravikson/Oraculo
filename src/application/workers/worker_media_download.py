from __future__ import annotations
import asyncio, base64, logging, os
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)

# WhatsApp/Evolution costuma recusar ou truncar vídeo grande enviado como
# payload base64 (limite prático bem abaixo do teto de 50MB do download em
# `MediaDownloadService`) — cap conservador só pro envio, não pro download.
_MAX_ENVIO_MB = 16


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
    audio_only = event.get("audio_only", False)
    result = await svc.download_youtube(event["url"], audio_only=audio_only)

    # Vídeo baixou mas passou do cap de envio — tenta de novo só o áudio
    # (mp3, geralmente bem menor) em vez de deixar parado em /tmp sem
    # nunca chegar no WhatsApp. Só faz sentido se ainda não era audio_only.
    rebaixado_pra_audio = False
    if result.ok and not audio_only and result.file_size_mb > _MAX_ENVIO_MB:
        logger.info("📥 [YTB] '%s' tem %.1fMB (> %dMB) — tentando só o áudio",
                    result.title[:40] if result.title else "?", result.file_size_mb, _MAX_ENVIO_MB)
        if result.file_path and os.path.exists(result.file_path):
            os.remove(result.file_path)
        result_audio = await svc.download_youtube(event["url"], audio_only=True)
        if result_audio.ok:
            result = result_audio
            rebaixado_pra_audio = True
        # Se o fallback de áudio falhar, segue com o `result` original (que
        # já teve o arquivo de vídeo removido) — cai no ramo de erro abaixo
        # via checagem de tamanho de novo.

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
        logger.info("📥 [YTB] '%s' → %s (%.1fMB)",
                    result.title[:40] if result.title else "?", result.file_path, result.file_size_mb)
        # `chat_id` (JID de grupo/contato) é o destino de envio de verdade;
        # `session_id` (telefone "cru") é só fallback pra quem ainda não
        # manda `chat_id` no event — ver dispatcher.py Fast-Path MEDIA_DOWNLOAD.
        destino = event.get("chat_id") or event.get("session_id", "")
        enviado = await _enviar_para_whatsapp(destino, result)
        if enviado:
            extra = " (só o áudio — o vídeo era grande demais pro WhatsApp)" if rebaixado_pra_audio else ""
            payload["answer"] = f"✅ **{result.title}** enviado 🎬{extra}"
        else:
            motivo_extra = ", mesmo só com áudio" if rebaixado_pra_audio else ""
            payload["answer"] = (
                f"❌ Baixei, mas não coube no envio direto do WhatsApp "
                f"({result.file_size_mb:.1f}MB > {_MAX_ENVIO_MB}MB{motivo_extra}).\n"
                f"🎬 **{result.title}**"
            )

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


async def _enviar_para_whatsapp(chat_id: str, result) -> bool:
    """Lê o arquivo baixado (`result.file_path`), converte pra base64 e manda
    via `EvolutionAdapter.enviar_midia_base64` — não há S3/URL pública na
    frente (decisão desta rodada: teste rápido, sem custo de storage extra).
    `chat_id` precisa ser o JID de verdade (`...@g.us` grupo ou
    `...@s.whatsapp.net` contato) — passar o telefone "cru" (session_id de
    memória) faz a Evolution API rejeitar com 400 ("jid não existe"; bug real
    encontrado nesta sessão, ver dispatcher.py Fast-Path MEDIA_DOWNLOAD).
    Sempre deleta o arquivo local ao final (sucesso ou falha), mesmo
    lifecycle documentado em `MediaDownloadService` ("Salva em /tmp —
    lifecycle: deletar após envio"). Devolve `False` sem tentar enviar se o
    arquivo passar de `_MAX_ENVIO_MB` (WhatsApp/Evolution não é confiável com
    payload base64 grande demais)."""
    if not chat_id or result.file_size_mb > _MAX_ENVIO_MB:
        if result.file_path and os.path.exists(result.file_path):
            os.remove(result.file_path)
        return False

    try:
        with open(result.file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
        gateway = EvolutionAdapter()
        mediatype = "audio" if result.media_type == "audio" else "video"
        mimetype = "audio/mpeg" if result.media_type == "audio" else "video/mp4"
        ok = await gateway.enviar_midia_base64(
            chat_id, b64, mediatype=mediatype, mimetype=mimetype,
            caption=result.title,
        )
        return ok
    except Exception as e:
        logger.warning("⚠️ [YTB] Falha ao enviar mídia pro WhatsApp: %s", str(e)[:200])
        return False
    finally:
        if result.file_path and os.path.exists(result.file_path):
            os.remove(result.file_path)


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