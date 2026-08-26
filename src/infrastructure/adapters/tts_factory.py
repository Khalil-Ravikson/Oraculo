"""
infrastructure/adapters/tts_factory.py — Seleção de ITextToSpeechProvider por config
=====================================================================================
Único ponto que sabe traduzir `settings.TTS_PROVIDER` (string) em uma instância
concreta. Consumidores (AudioService) só conhecem ITextToSpeechProvider.
"""
from __future__ import annotations

from src.domain.ports.text_to_speech_provider import ITextToSpeechProvider


def get_tts_provider(provider_name: str | None = None) -> ITextToSpeechProvider:
    """
    Retorna o provider de TTS configurado.

    provider_name: override explícito (útil em testes); se omitido, usa
    settings.TTS_PROVIDER.
    """
    from src.infrastructure.settings import settings

    name = (provider_name or settings.TTS_PROVIDER).strip().lower()

    if name == "kokoro":
        from src.infrastructure.adapters.kokoro_tts_provider import get_kokoro_provider
        return get_kokoro_provider()

    if name == "gtts":
        from src.infrastructure.adapters.gtts_provider import get_gtts_provider
        return get_gtts_provider()

    raise ValueError(f"TTS_PROVIDER desconhecido: '{name}'. Opções válidas: kokoro, gtts.")
