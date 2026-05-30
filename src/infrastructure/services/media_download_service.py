"""
MediaDownloadService — YouTube e Instagram.
Usa yt-dlp (ytb + insta) e instaloader como fallback para Instagram.
Salva em /tmp — lifecycle: deletar após envio.
"""
from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_TMP = "/tmp/oraculo_media"
os.makedirs(_TMP, exist_ok=True)


@dataclass
class MediaResult:
    ok: bool
    file_path: str = ""
    title: str = ""
    duration_s: int = 0
    file_size_mb: float = 0.0
    media_type: str = ""   # "video" | "audio" | "image"
    error: str = ""


class MediaDownloadService:

    _MAX_DURATION_S = 600   # 10 min — proteção contra downloads gigantes
    _MAX_SIZE_MB    = 50

    # ── YouTube ────────────────────────────────────────────────────────────────

    async def download_youtube(
        self, url: str, audio_only: bool = False
    ) -> MediaResult:
        return await asyncio.to_thread(self._ytb_sync, url, audio_only)

    def _ytb_sync(self, url: str, audio_only: bool) -> MediaResult:
        try:
            import yt_dlp
        except ImportError:
            return MediaResult(ok=False, error="yt-dlp não instalado: pip install yt-dlp")

        outfile = os.path.join(_TMP, "%(id)s.%(ext)s")
        fmt = "bestaudio/best" if audio_only else "best[filesize<50M]/best"

        ydl_opts = {
            "format":        fmt,
            "outtmpl":       outfile,
            "quiet":         True,
            "no_warnings":   True,
            "max_filesize":  self._MAX_SIZE_MB * 1024 * 1024,
        }
        if audio_only:
            ydl_opts["postprocessors"] = [{
                "key":            "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                duration = info.get("duration", 0)
                if duration > self._MAX_DURATION_S:
                    return MediaResult(ok=False, error=f"Vídeo muito longo ({duration}s > {self._MAX_DURATION_S}s)")

                filepath = ydl.prepare_filename(info)
                if audio_only:
                    filepath = os.path.splitext(filepath)[0] + ".mp3"

                size_mb = os.path.getsize(filepath) / 1024 / 1024
                return MediaResult(
                    ok=True, file_path=filepath,
                    title=info.get("title", "")[:200],
                    duration_s=duration,
                    file_size_mb=round(size_mb, 2),
                    media_type="audio" if audio_only else "video",
                )
        except Exception as e:
            return MediaResult(ok=False, error=str(e)[:300])

    # ── Instagram ──────────────────────────────────────────────────────────────

    async def download_instagram(self, url: str) -> MediaResult:
        """
        Baixa post/reel/foto do Instagram via yt-dlp.
        Para contas públicas funciona sem login.
        Para contas privadas: configure IG_USERNAME/IG_PASSWORD no .env.
        """
        return await asyncio.to_thread(self._insta_sync, url)

    def _insta_sync(self, url: str) -> MediaResult:
        try:
            import yt_dlp
        except ImportError:
            return MediaResult(ok=False, error="yt-dlp não instalado")

        from src.infrastructure.settings import settings

        ydl_opts = {
            "outtmpl":     os.path.join(_TMP, "%(id)s.%(ext)s"),
            "quiet":       True,
            "no_warnings": True,
            "max_filesize": self._MAX_SIZE_MB * 1024 * 1024,
        }
        ig_user = getattr(settings, "IG_USERNAME", "")
        ig_pass = getattr(settings, "IG_PASSWORD", "")
        if ig_user and ig_pass:
            ydl_opts["username"] = ig_user
            ydl_opts["password"] = ig_pass

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = ydl.prepare_filename(info)
                size_mb = os.path.getsize(filepath) / 1024 / 1024
                ext = os.path.splitext(filepath)[1].lower()
                mtype = "image" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"
                return MediaResult(
                    ok=True, file_path=filepath,
                    title=info.get("title", "")[:200],
                    file_size_mb=round(size_mb, 2),
                    media_type=mtype,
                )
        except Exception as e:
            return MediaResult(ok=False, error=str(e)[:300])


_svc: MediaDownloadService | None = None

def get_media_service() -> MediaDownloadService:
    global _svc
    if _svc is None:
        _svc = MediaDownloadService()
    return _svc