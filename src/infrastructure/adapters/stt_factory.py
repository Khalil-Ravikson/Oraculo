"""
infrastructure/adapters/stt_factory.py — Seleção de ISpeechToTextProvider por config
=====================================================================================
Único ponto que sabe traduzir `settings.STT_PROVIDER` (string) em uma instância
concreta. Consumidores (AudioService) só conhecem ISpeechToTextProvider.
"""
from __future__ import annotations

from src.domain.ports.speech_to_text_provider import ISpeechToTextProvider


def get_stt_provider(provider_name: str | None = None) -> ISpeechToTextProvider:
    """
    Retorna o provider de STT configurado.

    provider_name: override explícito (útil em testes); se omitido, usa
    settings.STT_PROVIDER.
    """
    from src.infrastructure.settings import settings

    name = (provider_name or settings.STT_PROVIDER).strip().lower()

    if name == "gemini":
        from src.infrastructure.adapters.gemini_stt_provider import get_gemini_stt_provider
        return get_gemini_stt_provider()

    raise ValueError(f"STT_PROVIDER desconhecido: '{name}'. Opções válidas: gemini.")
