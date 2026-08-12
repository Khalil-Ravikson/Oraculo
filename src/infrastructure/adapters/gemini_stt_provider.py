"""
infrastructure/adapters/gemini_stt_provider.py — GeminiSTTProvider
=====================================================================================
Implementa ISpeechToTextProvider via Gemini (suporte nativo a áudio, google-genai).
RESPONSABILIDADE ÚNICA: transcrição de áudio via Gemini.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from src.domain.ports.speech_to_text_provider import ISpeechToTextProvider, TranscriptionResult

logger = logging.getLogger(__name__)


class GeminiSTTProvider:
    """
    Provider de STT via Gemini (Part.from_bytes, suporte nativo a áudio).
    Implementa ISpeechToTextProvider — o domínio nunca importa google.genai diretamente.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        import google.genai as genai
        from src.infrastructure.settings import settings

        self._model  = model or settings.GEMINI_MODEL
        self._client = genai.Client(api_key=api_key or settings.GEMINI_API_KEY)

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type:   str = "audio/ogg",
    ) -> TranscriptionResult:
        """
        Transcreve áudio usando o suporte nativo a áudio do Gemini.
        mime_type: audio/ogg | audio/mp4 | audio/wav | audio/webm
        """
        from google.genai import types

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    "Transcreva o áudio acima para texto em português. "
                    "Retorne apenas a transcrição, sem comentários.",
                ],
            )
            text = (response.text or "").strip()
            if not text:
                return TranscriptionResult(ok=False, error="Transcrição vazia")
            return TranscriptionResult(ok=True, text=text)

        except Exception as exc:
            logger.exception("❌ GeminiSTTProvider.transcribe | erro: %s", exc)
            return TranscriptionResult(ok=False, error=str(exc)[:200])


# ─── Singleton ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_default_provider() -> GeminiSTTProvider:
    return GeminiSTTProvider()


def get_gemini_stt_provider() -> GeminiSTTProvider:
    """Retorna o singleton do GeminiSTTProvider."""
    return _get_default_provider()
