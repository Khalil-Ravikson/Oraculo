import pytest
from unittest.mock import MagicMock, patch

from src.domain.ports.text_to_speech_provider import SynthesisResult
from src.infrastructure.adapters.gtts_provider import GTTSProvider, get_gtts_provider


@pytest.mark.asyncio
async def test_synthesize_sucesso():
    provider = GTTSProvider()
    mock_gtts_instance = MagicMock()

    with patch("gtts.gTTS", return_value=mock_gtts_instance) as mock_gtts_cls:
        result = await provider.synthesize("Olá, tudo bem?", lang="pt")

    assert isinstance(result, SynthesisResult)
    assert result.ok is True
    assert result.audio_path.endswith(".mp3")
    assert result.error == ""
    mock_gtts_cls.assert_called_once()
    mock_gtts_instance.save.assert_called_once_with(result.audio_path)


@pytest.mark.asyncio
async def test_synthesize_trunca_texto_longo():
    provider = GTTSProvider()
    mock_gtts_instance = MagicMock()
    texto_longo = "a" * 1000

    with patch("gtts.gTTS", return_value=mock_gtts_instance) as mock_gtts_cls:
        await provider.synthesize(texto_longo, lang="pt")

    _, kwargs = mock_gtts_cls.call_args
    assert len(kwargs["text"]) == 500


@pytest.mark.asyncio
async def test_synthesize_excecao_retorna_erro_sem_estourar():
    provider = GTTSProvider()

    with patch("gtts.gTTS", side_effect=RuntimeError("network down")):
        result = await provider.synthesize("teste")

    assert result.ok is False
    assert "network down" in result.error


def test_get_gtts_provider_e_singleton():
    from src.infrastructure.adapters import gtts_provider as mod

    mod._get_default_provider.cache_clear()
    try:
        p1 = get_gtts_provider()
        p2 = get_gtts_provider()
        assert p1 is p2
    finally:
        mod._get_default_provider.cache_clear()
