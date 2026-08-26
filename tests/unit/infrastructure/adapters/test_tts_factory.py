import pytest
from unittest.mock import MagicMock, patch

from src.infrastructure.adapters.tts_factory import get_tts_provider


def test_get_tts_provider_kokoro_via_settings(monkeypatch):
    from src.infrastructure.settings import settings

    monkeypatch.setattr(settings, "TTS_PROVIDER", "kokoro")
    fake_instance = MagicMock()

    with patch(
        "src.infrastructure.adapters.kokoro_tts_provider.get_kokoro_provider",
        return_value=fake_instance,
    ) as mock_get:
        provider = get_tts_provider()

    assert provider is fake_instance
    mock_get.assert_called_once()


def test_get_tts_provider_gtts_via_settings(monkeypatch):
    from src.infrastructure.settings import settings

    monkeypatch.setattr(settings, "TTS_PROVIDER", "gtts")
    fake_instance = MagicMock()

    with patch(
        "src.infrastructure.adapters.gtts_provider.get_gtts_provider",
        return_value=fake_instance,
    ) as mock_get:
        provider = get_tts_provider()

    assert provider is fake_instance
    mock_get.assert_called_once()


def test_get_tts_provider_override_explicito_e_case_insensitive():
    fake_instance = MagicMock()

    with patch(
        "src.infrastructure.adapters.gtts_provider.get_gtts_provider",
        return_value=fake_instance,
    ):
        provider = get_tts_provider("GTTS")

    assert provider is fake_instance


def test_get_tts_provider_desconhecido_levanta_erro_claro():
    with pytest.raises(ValueError, match="TTS_PROVIDER desconhecido"):
        get_tts_provider("modelo-inexistente")
