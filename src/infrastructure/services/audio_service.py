"""
AudioService — orquestra STT/TTS via providers configuráveis.

Não fala com Gemini/gTTS diretamente — delega para o provider selecionado
por settings.STT_PROVIDER/TTS_PROVIDER (ver infrastructure/adapters/
{stt,tts}_factory.py). Trocar provider é mudança de config, não de código.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioResult:
    ok: bool
    text: str = ""
    audio_path: str = ""
    error: str = ""


class AudioService:

    # ── STT: áudio → texto ──────────────────────────────────────────────────

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> AudioResult:
        """
        Transcreve áudio via o provider de STT configurado (settings.STT_PROVIDER).
        mime_type: audio/ogg | audio/mp4 | audio/wav | audio/webm
        """
        from src.infrastructure.adapters.stt_factory import get_stt_provider

        provider = get_stt_provider()
        result = await provider.transcribe(audio_bytes, mime_type)
        return AudioResult(ok=result.ok, text=result.text, error=result.error)

    # ── TTS: texto → áudio ──────────────────────────────────────────────────

    async def synthesize(self, text: str, lang: str = "pt") -> AudioResult:
        """
        Converte texto em áudio via o provider de TTS configurado (settings.TTS_PROVIDER).
        Retorna caminho do arquivo temporário gerado.
        """
        from src.infrastructure.adapters.tts_factory import get_tts_provider

        provider = get_tts_provider()
        result = await provider.synthesize(text, lang)
        return AudioResult(ok=result.ok, audio_path=result.audio_path, error=result.error)


_audio_service: AudioService | None = None

def get_audio_service() -> AudioService:
    global _audio_service
    if _audio_service is None:
        _audio_service = AudioService()
    return _audio_service
