import pytest
from unittest.mock import MagicMock, patch

from src.infrastructure.adapters.stt_factory import get_stt_provider


def test_get_stt_provider_gemini_via_settings(monkeypatch):
    from src.infrastructure.settings import settings

    monkeypatch.setattr(settings, "STT_PROVIDER", "gemini")
    fake_instance = MagicMock()

    with patch(
        "src.infrastructure.adapters.gemini_stt_provider.get_gemini_stt_provider",
        return_value=fake_instance,
    ) as mock_get:
        provider = get_stt_provider()

    assert provider is fake_instance
    mock_get.assert_called_once()


def test_get_stt_provider_override_explicito_e_case_insensitive():
    fake_instance = MagicMock()

    with patch(
        "src.infrastructure.adapters.gemini_stt_provider.get_gemini_stt_provider",
        return_value=fake_instance,
    ):
        provider = get_stt_provider("GEMINI")

    assert provider is fake_instance


def test_get_stt_provider_desconhecido_levanta_erro_claro():
    with pytest.raises(ValueError, match="STT_PROVIDER desconhecido"):
        get_stt_provider("modelo-inexistente")
