import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.tasks.process_message_task import _enviar_resposta_em_audio


@pytest.mark.asyncio
async def test_enviar_resposta_em_audio_sucesso():
    fake_gateway = MagicMock()
    fake_gateway.enviar_midia_base64 = AsyncMock(return_value=True)

    with patch(
        "src.application.tasks.process_message_task.get_redis_text",
        return_value=MagicMock(),
    ), patch(
        "src.application.workers.registry.dispatch", return_value="task-abc"
    ) as mock_dispatch, patch(
        "src.capabilities.persistence.redis_state.get_result_cache",
        new_callable=AsyncMock,
        return_value={"status": "ok", "audio_b64": "ZmFrZS13YXY="},
    ):
        await _enviar_resposta_em_audio(fake_gateway, "5598@g.us", "resposta de teste")

    mock_dispatch.assert_called_once()
    worker_name, event = mock_dispatch.call_args.args
    assert worker_name == "text_to_audio"
    assert event["text"] == "resposta de teste"
    assert event["session_id"] == "5598@g.us"

    fake_gateway.enviar_midia_base64.assert_awaited_once()
    args, kwargs = fake_gateway.enviar_midia_base64.call_args
    assert args[0] == "5598@g.us"
    assert args[1] == "ZmFrZS13YXY="
    assert kwargs["mediatype"] == "audio"
    assert kwargs["mimetype"] == "audio/mpeg"
    assert kwargs["filename"] == "resposta.mp3"


@pytest.mark.asyncio
async def test_enviar_resposta_em_audio_worker_nao_encontrado_nao_envia():
    fake_gateway = MagicMock()
    fake_gateway.enviar_midia_base64 = AsyncMock()

    with patch(
        "src.application.tasks.process_message_task.get_redis_text",
        return_value=MagicMock(),
    ), patch("src.application.workers.registry.dispatch", return_value=None):
        await _enviar_resposta_em_audio(fake_gateway, "5598@g.us", "resposta de teste")

    fake_gateway.enviar_midia_base64.assert_not_awaited()


@pytest.mark.asyncio
async def test_enviar_resposta_em_audio_falha_sintese_nao_envia():
    fake_gateway = MagicMock()
    fake_gateway.enviar_midia_base64 = AsyncMock()

    with patch(
        "src.application.tasks.process_message_task.get_redis_text",
        return_value=MagicMock(),
    ), patch(
        "src.application.workers.registry.dispatch", return_value="task-abc"
    ), patch(
        "src.capabilities.persistence.redis_state.get_result_cache",
        new_callable=AsyncMock,
        return_value={"status": "error", "error": "modelo indisponível"},
    ):
        await _enviar_resposta_em_audio(fake_gateway, "5598@g.us", "resposta de teste")

    fake_gateway.enviar_midia_base64.assert_not_awaited()


@pytest.mark.asyncio
async def test_enviar_resposta_em_audio_timeout_nao_envia():
    fake_gateway = MagicMock()
    fake_gateway.enviar_midia_base64 = AsyncMock()

    with patch(
        "src.application.tasks.process_message_task.get_redis_text",
        return_value=MagicMock(),
    ), patch(
        "src.application.workers.registry.dispatch", return_value="task-abc"
    ), patch(
        "src.capabilities.persistence.redis_state.get_result_cache",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.application.tasks.process_message_task._TTS_TIMEOUT_S", 0.01
    ):
        await _enviar_resposta_em_audio(fake_gateway, "5598@g.us", "resposta de teste")

    fake_gateway.enviar_midia_base64.assert_not_awaited()
