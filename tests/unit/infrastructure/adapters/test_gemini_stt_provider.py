import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.domain.ports.speech_to_text_provider import TranscriptionResult
from src.infrastructure.adapters.gemini_stt_provider import (
    GeminiSTTProvider,
    get_gemini_stt_provider,
)


def _make_provider(response_text):
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text=response_text)
        )
        mock_client_cls.return_value = mock_client
        provider = GeminiSTTProvider(model="gemini-2.5-flash", api_key="fake-key")
    return provider, mock_client


@pytest.mark.asyncio
async def test_transcribe_sucesso():
    provider, mock_client = _make_provider("estou com erro no sistema")

    result = await provider.transcribe(b"fake-audio-bytes", mime_type="audio/ogg")

    assert isinstance(result, TranscriptionResult)
    assert result.ok is True
    assert result.text == "estou com erro no sistema"
    assert result.error == ""
    mock_client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_transcribe_texto_vazio_retorna_erro():
    provider, _ = _make_provider("")

    result = await provider.transcribe(b"fake-audio-bytes")

    assert result.ok is False
    assert "vazia" in result.error.lower()


@pytest.mark.asyncio
async def test_transcribe_excecao_retorna_erro_sem_estourar():
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("timeout de rede")
        )
        mock_client_cls.return_value = mock_client
        provider = GeminiSTTProvider(model="gemini-2.5-flash", api_key="fake-key")

    result = await provider.transcribe(b"fake-audio-bytes")

    assert result.ok is False
    assert "timeout de rede" in result.error


def test_get_gemini_stt_provider_e_singleton():
    from src.infrastructure.adapters import gemini_stt_provider as mod

    mod._get_default_provider.cache_clear()
    try:
        with patch("google.genai.Client"):
            p1 = get_gemini_stt_provider()
            p2 = get_gemini_stt_provider()
        assert p1 is p2
    finally:
        mod._get_default_provider.cache_clear()
