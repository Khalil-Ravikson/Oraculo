"""
infrastructure/adapters/kokoro_tts_provider.py — KokoroTTSProvider
=====================================================================================
Implementa ITextToSpeechProvider via Kokoro-82M (Apache-2.0, CPU, offline em
runtime — modelo baked na imagem no build, ver Dockerfile).
RESPONSABILIDADE ÚNICA: síntese de voz via Kokoro.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from functools import lru_cache

from src.domain.ports.text_to_speech_provider import ITextToSpeechProvider, SynthesisResult

logger = logging.getLogger(__name__)


class KokoroTTSProvider:
    """
    Provider de TTS via Kokoro-82M (StyleTTS2, pesos Apache-2.0). CPU, sem
    chave, sem internet em runtime. Implementa ITextToSpeechProvider.

    Pipeline carregado lazy (custa ~15s na 1ª chamada por processo — modelo
    fica em memória depois) em vez de no __init__, pra não pagar esse custo
    em workers que nunca chamam TTS.
    """

    def __init__(self, voice: str | None = None) -> None:
        from src.infrastructure.settings import settings
        self._voice = voice or settings.KOKORO_VOICE
        self._pipeline = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            logger.info("🔊 [KOKORO] Carregando pipeline (lang_code='p')...")
            self._pipeline = KPipeline(lang_code="p")
        return self._pipeline

    async def synthesize(
        self,
        text: str,
        lang: str = "pt",
    ) -> SynthesisResult:
        """Converte texto em áudio MP3 via Kokoro. Retorna caminho do arquivo temporário."""
        try:
            path = await asyncio.to_thread(self._synthesize_sync, text)
            return SynthesisResult(ok=True, audio_path=path)
        except Exception as exc:
            logger.exception("❌ KokoroTTSProvider.synthesize | erro: %s", exc)
            return SynthesisResult(ok=False, error=str(exc)[:200])

    def _synthesize_sync(self, text: str) -> str:
        """
        Kokoro gera WAV cru (float32, 24kHz). Codifica pra MP3 antes de
        salvar — WhatsApp/Evolution API aceita "audio/wav" com HTTP 201 mas
        nunca entrega de verdade (bug real encontrado testando ao vivo, ver
        notas.md seção 12); audio/mpeg é o formato que o único outro envio
        de áudio deste projeto (worker_media_download.py) já usa. `lameenc`
        é puro wheel (sem precisar de ffmpeg via apt).
        """
        import lameenc
        import numpy as np

        pipeline = self._get_pipeline()
        chunks = []
        for _gs, _ps, audio in pipeline(text[:500], voice=self._voice):
            chunks.append(audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio)
        if not chunks:
            raise RuntimeError("Kokoro não gerou áudio")
        audio_full = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

        pcm16 = (np.clip(audio_full, -1.0, 1.0) * 32767).astype(np.int16)
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(64)
        encoder.set_in_sample_rate(24000)
        encoder.set_channels(1)
        encoder.set_quality(2)
        mp3_bytes = encoder.encode(pcm16.tobytes())
        mp3_bytes += encoder.flush()

        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir="/tmp")
        with open(tmp.name, "wb") as f:
            f.write(mp3_bytes)
        return tmp.name


# ─── Singleton ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_default_provider() -> KokoroTTSProvider:
    return KokoroTTSProvider()


def get_kokoro_provider() -> KokoroTTSProvider:
    """Retorna o singleton do KokoroTTSProvider."""
    return _get_default_provider()
