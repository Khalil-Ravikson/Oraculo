from __future__ import annotations
from typing import Protocol
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    """Envelope padrão para qualquer resultado de transcrição de áudio."""
    ok:    bool
    text:  str = ""
    error: str = ""


class ISpeechToTextProvider(Protocol):
    """
    O Oráculo só conversa com esta interface para transcrever áudio.
    Ele não sabe se é Gemini, faster-whisper ou outro provider.
    """

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type:   str = "audio/ogg",
    ) -> TranscriptionResult:
        """Transcreve áudio para texto em português."""
        ...
