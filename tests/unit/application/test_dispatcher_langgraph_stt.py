import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.orchestration.entrypoint import processar


@pytest.fixture(autouse=True)
def _sem_redis_hitl_legado(monkeypatch):
    """`entrypoint.processar()` chama handle_hitl_continuation (guardrails+HITL
    legado) antes de rotear — isso abre uma conexão real ao Redis via
    redis_state.get_hitl_session(). Nenhum teste deste arquivo cobre esse
    fluxo (SIGAA CPF/senha), então mocka "sem sessão pendente" sem tocar
    Redis de verdade.

    Também neutraliza o rate limit real de InputGuardrail (mesmo commit) —
    ver comentário equivalente em test_langgraph_ticket_hitl.py."""
    async def _sem_sessao(*a, **k):
        return None

    monkeypatch.setattr(
        "src.capabilities.persistence.redis_state.get_hitl_session", _sem_sessao,
    )
    monkeypatch.setattr(
        "src.application.chain.guardrails.InputGuardrail._check_rate_limit",
        lambda self, user_id, r: (False, ""),
    )


@pytest.mark.asyncio
async def test_audio_transcrito_antes_de_rotear_e_antes_do_grafo(monkeypatch):
    """
    Bug real encontrado em produção (2026-08-12): a interceptação de áudio
    ficava num fast-path que não rodava pro caso mais comum (voice note →
    GERAL). `rotear("", ...)` reclassificava a mensagem vazia e o RAG ia pro
    embedding com query vazia. Este teste garante que a transcrição acontece
    ANTES do grafo — `classify_node` (ADR 0008 Fase B, dentro do grafo) recebe
    o texto transcrito, não a mensagem vazia original.

    Usa o grafo REAL (`build_graph()`, MemorySaver) em vez de mockar
    `_get_graph()`: como a classificação agora roda DENTRO do `ainvoke()`
    (era uma chamada direta do entrypoint antes da Fase B), só dá pra
    observar o texto que `rotear()` recebeu deixando o `classify_node`
    executar de verdade."""
    import src.application.orchestration.entrypoint as ep
    from src.application.orchestration.builder import build_graph

    monkeypatch.setattr(ep, "_graph", build_graph())

    user_context = {"media_type": "audioMessage", "msg_key_id": "MSG123", "chat_id": "5598@g.us"}

    with patch(
        "src.application.runtime.audio_intake._transcrever_audio_recebido",
        new_callable=AsyncMock,
        return_value="estou com erro no sistema",
    ), patch(
        "src.router.supervisor.rotear", new_callable=AsyncMock
    ) as mock_rotear, patch(
        "src.capabilities.persistence.agent_config.is_agent_enabled",
        new_callable=AsyncMock, return_value=True,
    ), patch(
        "src.application.orchestration.nodes.responder_rag_direto",
        new_callable=AsyncMock, return_value="resposta do RAG",
    ):
        mock_decision = MagicMock()
        mock_decision.rota = "GERAL"
        mock_rotear.return_value = mock_decision

        result = await processar("", "session-1", user_context)

    # rotear() precisa ter recebido o texto transcrito, não a mensagem vazia original.
    mock_rotear.assert_awaited_once()
    assert mock_rotear.call_args.args[0] == "estou com erro no sistema"
    assert result.answer == "resposta do RAG"


@pytest.mark.asyncio
async def test_transcricao_falha_retorna_erro_sem_chamar_grafo():
    user_context = {"media_type": "audioMessage", "msg_key_id": "MSG123"}

    with patch(
        "src.application.runtime.audio_intake._transcrever_audio_recebido",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.application.orchestration.entrypoint._get_graph",
        new_callable=AsyncMock,
    ) as mock_get_graph:
        result = await processar("", "session-1", user_context)

    assert result.status == "error"
    assert result.rota == "AUDIO_TRANSCRIBE"
    assert "áudio" in result.answer.lower()
    mock_get_graph.assert_not_called()


@pytest.mark.asyncio
async def test_imagem_sem_legenda_retorna_mensagem_amigavel_sem_chamar_grafo():
    user_context = {"has_media": True, "media_type": "imageMessage"}

    with patch(
        "src.application.orchestration.entrypoint._get_graph",
        new_callable=AsyncMock,
    ) as mock_get_graph, patch(
        "src.router.supervisor.rotear", new_callable=AsyncMock
    ) as mock_rotear:
        result = await processar("", "session-1", user_context)

    assert result.status == "ok"
    assert result.rota == "UNSUPPORTED_MEDIA"
    assert "imagens" in result.answer.lower()
    mock_get_graph.assert_not_called()
    mock_rotear.assert_not_called()


@pytest.mark.asyncio
async def test_grafo_recebe_media_type_limpo_apos_transcricao():
    """Depois de transcrever o áudio, o payload que entra no grafo precisa ter
    media_type/msg_key_id já limpos — senão um nó que olhasse o user_context
    tentaria baixar/transcrever o MESMO áudio de novo."""
    user_context = {"media_type": "audioMessage", "msg_key_id": "MSG123"}

    fake_app = MagicMock()
    fake_app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    fake_app.ainvoke = AsyncMock(return_value={"answer": "ok"})

    with patch(
        "src.application.runtime.audio_intake._transcrever_audio_recebido",
        new_callable=AsyncMock,
        return_value="quero consultar meu histórico",
    ), patch(
        "src.application.orchestration.entrypoint._get_graph",
        new_callable=AsyncMock,
        return_value=fake_app,
    ), patch(
        "src.router.supervisor.rotear", new_callable=AsyncMock
    ) as mock_rotear, patch(
        "src.capabilities.persistence.agent_config.is_agent_enabled",
        new_callable=AsyncMock, return_value=True,
    ):
        mock_decision = MagicMock()
        mock_decision.rota = "SIGAA"
        mock_rotear.return_value = mock_decision

        await processar("", "session-1", user_context)

    fake_app.ainvoke.assert_awaited_once()
    payload = fake_app.ainvoke.call_args.args[0]
    assert payload["message"] == "quero consultar meu histórico"
    assert payload["user_context"]["media_type"] == ""
    assert payload["user_context"]["msg_key_id"] == ""
