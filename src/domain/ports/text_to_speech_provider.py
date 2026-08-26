from __future__ import annotations
from typing import Protocol
from dataclasses import dataclass


@dataclass
class SynthesisResult:
    """Envelope padrão para qualquer resultado de síntese de voz."""
    ok:         bool
    audio_path: str = ""
    error:      str = ""


class ITextToSpeechProvider(Protocol):
    """
    O Oráculo só conversa com esta interface para sintetizar voz.
    Ele não sabe se é gTTS, Piper ou outro provider.
    """

    async def synthesize(
        self,
        text: str,
        lang: str = "pt",
    ) -> SynthesisResult:
        """Converte texto em áudio, retornando o caminho do arquivo gerado."""
        ...
