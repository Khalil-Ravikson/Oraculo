"""
infrastructure/adapters/gtts_provider.py — GTTSProvider
=====================================================================================
Implementa ITextToSpeechProvider via gTTS (Google Translate TTS, grátis, CPU).
RESPONSABILIDADE ÚNICA: síntese de voz via gTTS.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from functools import lru_cache

from src.domain.ports.text_to_speech_provider import ITextToSpeechProvider, SynthesisResult

logger = logging.getLogger(__name__)


class GTTSProvider:
    """
    Provider de TTS via gTTS (CPU, sem chave, sem clonagem de voz).
    Implementa ITextToSpeechProvider.
    """

    async def synthesize(
        self,
        text: str,
        lang: str = "pt",
    ) -> SynthesisResult:
        """Converte texto em áudio MP3 via gTTS. Retorna caminho do arquivo temporário."""
        try:
            path = await asyncio.to_thread(self._gtts_sync, text, lang)
            return SynthesisResult(ok=True, audio_path=path)
        except ImportError:
            return SynthesisResult(ok=False, error="gTTS não instalado: pip install gTTS")
        except Exception as exc:
            logger.exception("❌ GTTSProvider.synthesize | erro: %s", exc)
            return SynthesisResult(ok=False, error=str(exc)[:200])

    def _gtts_sync(self, text: str, lang: str) -> str:
        from gtts import gTTS
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir="/tmp")
        tts = gTTS(text=text[:500], lang=lang, slow=False)
        tts.save(tmp.name)
        return tmp.name


# ─── Singleton ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_default_provider() -> GTTSProvider:
    return GTTSProvider()


def get_gtts_provider() -> GTTSProvider:
    """Retorna o singleton do GTTSProvider."""
    return _get_default_provider()
