import pytest
from unittest.mock import MagicMock, patch
import numpy as np

from src.domain.ports.text_to_speech_provider import SynthesisResult
from src.infrastructure.adapters.kokoro_tts_provider import KokoroTTSProvider, get_kokoro_provider


@pytest.mark.asyncio
async def test_synthesize_sucesso():
    provider = KokoroTTSProvider(voice="pf_dora")
    fake_audio = np.zeros(1000, dtype=np.float32)
    fake_pipeline = MagicMock(return_value=[("gs", "ps", fake_audio)])

    with patch("kokoro.KPipeline", return_value=fake_pipeline) as mock_cls:
        result = await provider.synthesize("Olá, tudo bem?", lang="pt")

    assert isinstance(result, SynthesisResult)
    assert result.ok is True
    assert result.audio_path.endswith(".mp3")
    assert result.error == ""
    mock_cls.assert_called_once_with(lang_code="p")
    fake_pipeline.assert_called_once()
    call_args, call_kwargs = fake_pipeline.call_args
    assert call_args[0] == "Olá, tudo bem?"
    assert call_kwargs["voice"] == "pf_dora"

    # arquivo real gerado pelo lameenc — confere que é mesmo MP3 válido
    # (frame sync 0xFFEx), não WAV (bug real corrigido nesta sessão: Evolution
    # API aceitava "audio/wav" com HTTP 201 mas o áudio nunca chegava no
    # WhatsApp de verdade).
    with open(result.audio_path, "rb") as f:
        header = f.read(2)
    assert header[0] == 0xFF and (header[1] & 0xE0) == 0xE0


@pytest.mark.asyncio
async def test_synthesize_reaproveita_pipeline_ja_carregado():
    provider = KokoroTTSProvider(voice="pf_dora")
    fake_audio = np.zeros(500, dtype=np.float32)
    fake_pipeline = MagicMock(return_value=[("gs", "ps", fake_audio)])

    with patch("kokoro.KPipeline", return_value=fake_pipeline) as mock_cls:
        await provider.synthesize("primeira chamada")
        await provider.synthesize("segunda chamada")

    mock_cls.assert_called_once()  # pipeline só é instanciado uma vez por processo
    assert fake_pipeline.call_count == 2


@pytest.mark.asyncio
async def test_synthesize_trunca_texto_longo():
    provider = KokoroTTSProvider(voice="pf_dora")
    fake_audio = np.zeros(100, dtype=np.float32)
    fake_pipeline = MagicMock(return_value=[("gs", "ps", fake_audio)])
    texto_longo = "a" * 1000

    with patch("kokoro.KPipeline", return_value=fake_pipeline):
        await provider.synthesize(texto_longo)

    call_args, _ = fake_pipeline.call_args
    assert len(call_args[0]) == 500


@pytest.mark.asyncio
async def test_synthesize_excecao_retorna_erro_sem_estourar():
    provider = KokoroTTSProvider(voice="pf_dora")

    with patch("kokoro.KPipeline", side_effect=RuntimeError("modelo não encontrado")):
        result = await provider.synthesize("teste")

    assert result.ok is False
    assert "modelo não encontrado" in result.error


@pytest.mark.asyncio
async def test_synthesize_sem_audio_gerado_retorna_erro():
    provider = KokoroTTSProvider(voice="pf_dora")
    fake_pipeline = MagicMock(return_value=[])

    with patch("kokoro.KPipeline", return_value=fake_pipeline):
        result = await provider.synthesize("teste")

    assert result.ok is False


def test_get_kokoro_provider_e_singleton():
    from src.infrastructure.adapters import kokoro_tts_provider as mod

    mod._get_default_provider.cache_clear()
    try:
        p1 = get_kokoro_provider()
        p2 = get_kokoro_provider()
        assert p1 is p2
    finally:
        mod._get_default_provider.cache_clear()
