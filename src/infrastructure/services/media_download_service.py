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

    _MAX_DURATION_S = 3600   # 60 min — proteção contra downloads gigantes
    _MAX_SIZE_MB    = 50

    # ── YouTube ────────────────────────────────────────────────────────────────

    async def download_youtube(
        self, url: str, audio_only: bool = False
    ) -> MediaResult:
        return await asyncio.to_thread(self._ytb_sync, url, audio_only, True)

    async def get_youtube_metadata(self, url: str) -> MediaResult:
        return await asyncio.to_thread(self._ytb_sync, url, False, False)

    def _ytb_sync(self, url: str, audio_only: bool, download: bool) -> MediaResult:
        try:
            import yt_dlp
        except ImportError:
            return MediaResult(ok=False, error="yt-dlp não instalado: pip install yt-dlp")

        outfile = os.path.join(_TMP, "%(id)s.%(ext)s")
        fmt = "bestaudio/best" if audio_only else "best[filesize<50M]/best"

        def _filtro(info_dict, *, incomplete=False):
            # Roda DEPOIS da extração de metadata, ANTES do download de
            # verdade começar — sem isso, uma live 24/7 (fácil de cair sem
            # querer via `buscar vídeo <termo>`, ver src/router/supervisor.py)
            # seria baixada indefinidamente antes da checagem de duração
            # (que antes rodava só depois do download completo).
            if info_dict.get("is_live") or info_dict.get("was_live"):
                return "Transmissão ao vivo não suportada"
            dur = info_dict.get("duration") or 0
            if dur > self._MAX_DURATION_S:
                return f"Vídeo muito longo ({dur}s > {self._MAX_DURATION_S}s)"
            return None

        ydl_opts = {
            "format":        fmt,
            "outtmpl":       outfile,
            "quiet":         True,
            "no_warnings":   True,
            "max_filesize":  self._MAX_SIZE_MB * 1024 * 1024,
            "match_filter":  _filtro,
        }
        if audio_only:
            ydl_opts["postprocessors"] = [{
                "key":            "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=download)
                # `ytsearch1:...` devolve um dict de playlist com 1 item em
                # "entries" (não o vídeo direto) — normaliza pro entry real
                # antes de ler title/duration/preparar o filename.
                if info is not None and info.get("entries") is not None:
                    entradas = list(info.get("entries") or [])
                    if not entradas:
                        return MediaResult(ok=False, error=f"Nenhum vídeo encontrado para '{url.split(':', 1)[-1]}'")
                    info = entradas[0]

                duration = info.get("duration", 0)

                size_mb = 0.0
                if download:
                    filepath = ydl.prepare_filename(info)
                    if audio_only:
                        filepath = os.path.splitext(filepath)[0] + ".mp3"
                    if not os.path.exists(filepath):
                        # `match_filter` rejeitou (live ou vídeo longo demais)
                        # — yt-dlp só loga aviso, não levanta exceção.
                        motivo = "Transmissão ao vivo não suportada" if (info.get("is_live") or info.get("was_live")) \
                            else f"Vídeo muito longo ({duration}s > {self._MAX_DURATION_S}s)"
                        return MediaResult(ok=False, error=motivo)
                    size_mb = os.path.getsize(filepath) / 1024 / 1024
                else:
                    filepath = ""

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
        """
        return await asyncio.to_thread(self._insta_sync, url, True)

    async def get_instagram_metadata(self, url: str) -> MediaResult:
        return await asyncio.to_thread(self._insta_sync, url, False)

    def _insta_sync(self, url: str, download: bool) -> MediaResult:
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
                info = ydl.extract_info(url, download=download)
                
                size_mb = 0.0
                mtype = "video"
                filepath = ""
                if download:
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