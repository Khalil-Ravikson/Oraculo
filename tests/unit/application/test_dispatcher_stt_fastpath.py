import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.runtime.dispatcher import processar, _transcrever_audio_recebido


class MockRedis:
    def __init__(self):
        self.db = {}

    def get(self, key):
        val = self.db.get(key)
        if val is None:
            return None
        return val.encode("utf-8") if isinstance(val, str) else val

    def setex(self, key, time, value):
        self.db[key] = value

    def exists(self, key):
        return key in self.db

    def delete(self, key):
        self.db.pop(key, None)

    def xadd(self, name, fields, **kwargs):
        pass


# ── Testes unitários de _transcrever_audio_recebido() ──────────────────────


@pytest.mark.asyncio
async def test_transcrever_audio_sucesso():
    mock_redis = MockRedis()
    user_context = {"msg_key_id": "MSG123"}

    with patch(
        "src.infrastructure.adapters.evolution_adapter.EvolutionAdapter"
    ) as mock_gw_cls, patch(
        "src.application.workers.registry.dispatch", return_value="task-abc"
    ) as mock_dispatch, patch(
        "src.capabilities.persistence.redis_state.get_result_cache",
        new_callable=AsyncMock,
        return_value={"status": "ok", "transcription": "estou com erro no sistema"},
    ):
        mock_gw = MagicMock()
        mock_gw.baixar_midia_base64 = AsyncMock(return_value=("YWJj", "audio/ogg", "audio.ogg"))
        mock_gw_cls.return_value = mock_gw

        transcript = await _transcrever_audio_recebido(mock_redis, user_context, "session-1")

    assert transcript == "estou com erro no sistema"
    mock_dispatch.assert_called_once()
    worker_name, event = mock_dispatch.call_args.args
    assert worker_name == "audio_to_text"
    assert event["audio_b64"] == "YWJj"
    assert event["mime_type"] == "audio/ogg"


@pytest.mark.asyncio
async def test_transcrever_audio_download_vazio_retorna_none():
    mock_redis = MockRedis()
    user_context = {"msg_key_id": "MSG123"}

    with patch("src.infrastructure.adapters.evolution_adapter.EvolutionAdapter") as mock_gw_cls:
        mock_gw = MagicMock()
        mock_gw.baixar_midia_base64 = AsyncMock(return_value=("", "", ""))
        mock_gw_cls.return_value = mock_gw

        transcript = await _transcrever_audio_recebido(mock_redis, user_context, "session-1")

    assert transcript is None


@pytest.mark.asyncio
async def test_transcrever_audio_grande_demais_retorna_none():
    mock_redis = MockRedis()
    user_context = {"msg_key_id": "MSG123"}
    # base64 decodifica em len(b64)*3/4 bytes — precisa de ~22.3MB de chars
    # pra passar de 16MB decodificados. String de 'A' é suficiente, não
    # precisa ser base64 válido pra essa checagem de tamanho.
    b64_gigante = "A" * (24 * 1024 * 1024)

    with patch("src.infrastructure.adapters.evolution_adapter.EvolutionAdapter") as mock_gw_cls, \
         patch("src.application.workers.registry.dispatch") as mock_dispatch:
        mock_gw = MagicMock()
        mock_gw.baixar_midia_base64 = AsyncMock(return_value=(b64_gigante, "audio/ogg", "x.ogg"))
        mock_gw_cls.return_value = mock_gw

        transcript = await _transcrever_audio_recebido(mock_redis, user_context, "session-1")

    assert transcript is None
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_transcrever_audio_worker_nao_encontrado_retorna_none():
    mock_redis = MockRedis()
    user_context = {"msg_key_id": "MSG123"}

    with patch("src.infrastructure.adapters.evolution_adapter.EvolutionAdapter") as mock_gw_cls, \
         patch("src.application.workers.registry.dispatch", return_value=None):
        mock_gw = MagicMock()
        mock_gw.baixar_midia_base64 = AsyncMock(return_value=("YWJj", "audio/ogg", "x.ogg"))
        mock_gw_cls.return_value = mock_gw

        transcript = await _transcrever_audio_recebido(mock_redis, user_context, "session-1")

    assert transcript is None


@pytest.mark.asyncio
async def test_transcrever_audio_timeout_retorna_none():
    mock_redis = MockRedis()
    user_context = {"msg_key_id": "MSG123"}

    with patch("src.infrastructure.adapters.evolution_adapter.EvolutionAdapter") as mock_gw_cls, \
         patch("src.application.workers.registry.dispatch", return_value="task-abc"), \
         patch(
             "src.capabilities.persistence.redis_state.get_result_cache",
             new_callable=AsyncMock,
             return_value=None,
         ), \
         patch("src.application.runtime.dispatcher._STT_TIMEOUT_S", 0.01):
        mock_gw = MagicMock()
        mock_gw.baixar_midia_base64 = AsyncMock(return_value=("YWJj", "audio/ogg", "x.ogg"))
        mock_gw_cls.return_value = mock_gw

        transcript = await _transcrever_audio_recebido(mock_redis, user_context, "session-1")

    assert transcript is None


# ── Testes de integração via processar() ────────────────────────────────────


@pytest.mark.asyncio
async def test_processar_com_audio_substitui_message_pelo_transcript():
    mock_redis = MockRedis()
    user_context = {"role": "student", "media_type": "audioMessage", "msg_key_id": "MSG123"}

    mock_decision = MagicMock()
    mock_decision.rota = "GERAL"
    mock_decision.cache_hit = False
    mock_decision.dag_hint = {}

    # get_cognitive_memory() usa um cliente Redis async separado (real,
    # não o get_redis_text() síncrono acima) — sem Redis rodando neste
    # ambiente de dev, precisa de fake pra não depender de infra externa
    # (mesma limitação de ambiente já documentada nos testes pré-existentes
    # de test_dispatcher.py, que exigem Docker/Redis real).
    fake_mem = MagicMock()
    fake_mem.get_operational   = AsyncMock(return_value={})
    fake_mem.format_history    = AsyncMock(return_value="")
    fake_mem.get_task_history  = AsyncMock(return_value={})
    fake_mem.set_operational   = AsyncMock(return_value=None)
    fake_mem.add_turn          = AsyncMock(return_value=None)

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch(
             "src.application.runtime.dispatcher._transcrever_audio_recebido",
             new_callable=AsyncMock,
             return_value="estou com erro no sistema",
         ), \
         patch("src.memory.services.redis_memory_service.get_cognitive_memory", return_value=fake_mem), \
         patch("src.router.supervisor.rotear", new_callable=AsyncMock, return_value=mock_decision) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled", new_callable=AsyncMock, return_value=True):

        await processar("", "session-1", user_context)

    # O router precisa ter recebido o texto transcrito, não a mensagem vazia original.
    assert mock_rotear.call_args.args[0] == "estou com erro no sistema"


@pytest.mark.asyncio
async def test_processar_com_imagem_sem_legenda_retorna_mensagem_amigavel():
    """Vision ainda não existe — imagem/sticker/vídeo/documento sem legenda
    não pode seguir com message="" até o RAG (mesma classe de bug do áudio,
    achado real testando com imagens de verdade)."""
    mock_redis = MockRedis()
    user_context = {"role": "student", "has_media": True, "media_type": "imageMessage"}

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis):
        result = await processar("", "session-1", user_context)

    assert result.status == "ok"
    assert result.rota == "UNSUPPORTED_MEDIA"
    assert "imagens" in result.answer.lower()


@pytest.mark.asyncio
async def test_processar_com_audio_falha_retorna_mensagem_amigavel():
    mock_redis = MockRedis()
    user_context = {"role": "student", "media_type": "audioMessage", "msg_key_id": "MSG123"}

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch(
             "src.application.runtime.dispatcher._transcrever_audio_recebido",
             new_callable=AsyncMock,
             return_value=None,
         ):
        result = await processar("", "session-1", user_context)

    assert result.status == "error"
    assert result.rota == "AUDIO_TRANSCRIBE"
    assert "áudio" in result.answer.lower()
