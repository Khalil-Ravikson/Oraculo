"""
AudioService — STT via Gemini 2.0, TTS via gTTS.
Sem GPU. Sem modelos locais pesados.
"""
from __future__ import annotations
import asyncio
import base64
import logging
import os
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioResult:
    ok: bool
    text: str = ""
    audio_path: str = ""
    error: str = ""


class AudioService:

    # ── STT: áudio → texto via Gemini ─────────────────────────────────────────

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/ogg") -> AudioResult:
        """
        Transcreve áudio usando Gemini 2.0 Flash (suporte nativo a áudio).
        mime_type: audio/ogg | audio/mp4 | audio/wav | audio/webm
        """
        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            b64 = base64.b64encode(audio_bytes).decode()

            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    "Transcreva o áudio acima para texto em português. "
                    "Retorne apenas a transcrição, sem comentários.",
                ],
            )
            text = (response.text or "").strip()
            if not text:
                return AudioResult(ok=False, error="Transcrição vazia")
            return AudioResult(ok=True, text=text)

        except Exception as e:
            logger.exception("❌ [AUDIO] transcribe falhou: %s", e)
            return AudioResult(ok=False, error=str(e)[:200])

    # ── TTS: texto → áudio via gTTS ────────────────────────────────────────────

    async def synthesize(self, text: str, lang: str = "pt") -> AudioResult:
        """
        Converte texto em áudio MP3 via gTTS (CPU, gratuito).
        Retorna caminho do arquivo temporário.
        """
        try:
            import asyncio
            path = await asyncio.to_thread(self._gtts_sync, text, lang)
            return AudioResult(ok=True, audio_path=path)
        except ImportError:
            return AudioResult(ok=False, error="gTTS não instalado: pip install gTTS")
        except Exception as e:
            logger.exception("❌ [AUDIO] synthesize falhou: %s", e)
            return AudioResult(ok=False, error=str(e)[:200])

    def _gtts_sync(self, text: str, lang: str) -> str:
        from gtts import gTTS
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir="/tmp")
        tts = gTTS(text=text[:500], lang=lang, slow=False)
        tts.save(tmp.name)
        return tmp.name


_audio_service: AudioService | None = None

def get_audio_service() -> AudioService:
    global _audio_service
    if _audio_service is None:
        _audio_service = AudioService()
    return _audio_service