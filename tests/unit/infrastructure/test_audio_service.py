import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.infrastructure.services.audio_service import AudioService, AudioResult
from src.domain.ports.speech_to_text_provider import TranscriptionResult
from src.domain.ports.text_to_speech_provider import SynthesisResult


@pytest.mark.asyncio
async def test_transcribe_delega_para_provider_configurado():
    fake_provider = MagicMock()
    fake_provider.transcribe = AsyncMock(
        return_value=TranscriptionResult(ok=True, text="ola mundo")
    )

    with patch(
        "src.infrastructure.adapters.stt_factory.get_stt_provider",
        return_value=fake_provider,
    ):
        result = await AudioService().transcribe(b"audio-bytes", mime_type="audio/wav")

    assert isinstance(result, AudioResult)
    assert result.ok is True
    assert result.text == "ola mundo"
    fake_provider.transcribe.assert_awaited_once_with(b"audio-bytes", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_propaga_falha_do_provider():
    fake_provider = MagicMock()
    fake_provider.transcribe = AsyncMock(
        return_value=TranscriptionResult(ok=False, error="algo deu errado")
    )

    with patch(
        "src.infrastructure.adapters.stt_factory.get_stt_provider",
        return_value=fake_provider,
    ):
        result = await AudioService().transcribe(b"audio-bytes")

    assert result.ok is False
    assert result.error == "algo deu errado"


@pytest.mark.asyncio
async def test_synthesize_delega_para_provider_configurado():
    fake_provider = MagicMock()
    fake_provider.synthesize = AsyncMock(
        return_value=SynthesisResult(ok=True, audio_path="/tmp/x.mp3")
    )

    with patch(
        "src.infrastructure.adapters.tts_factory.get_tts_provider",
        return_value=fake_provider,
    ):
        result = await AudioService().synthesize("texto de teste", lang="pt")

    assert isinstance(result, AudioResult)
    assert result.ok is True
    assert result.audio_path == "/tmp/x.mp3"
    fake_provider.synthesize.assert_awaited_once_with("texto de teste", "pt")


@pytest.mark.asyncio
async def test_synthesize_propaga_falha_do_provider():
    fake_provider = MagicMock()
    fake_provider.synthesize = AsyncMock(
        return_value=SynthesisResult(ok=False, error="gTTS indisponível")
    )

    with patch(
        "src.infrastructure.adapters.tts_factory.get_tts_provider",
        return_value=fake_provider,
    ):
        result = await AudioService().synthesize("texto")

    assert result.ok is False
    assert result.error == "gTTS indisponível"
