import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.runtime.dispatcher_langgraph import processar


@pytest.mark.asyncio
async def test_audio_transcrito_antes_de_rotear_e_antes_do_grafo():
    """
    Bug real encontrado em produção (2026-08-12): a interceptação de áudio só
    existia em dispatcher.py::processar(), que dispatcher_langgraph.py só
    chama quando a rota classificada NÃO é uma das que ele trata direto
    (TICKET_ABERTURA/CRUD/RAG) — ou seja, nunca rodava pro caso mais comum
    (voice note → GERAL). `rotear("", ...)` reclassificava a mensagem vazia,
    e o RAG do LangGraph ia pro embedding com query vazia. Este teste garante
    que a transcrição acontece ANTES de `rotear()` e ANTES de `_get_graph()`
    ser sequer chamado com a rota errada.
    """
    user_context = {"media_type": "audioMessage", "msg_key_id": "MSG123", "chat_id": "5598@g.us"}

    fake_app = MagicMock()
    fake_app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    fake_app.ainvoke = AsyncMock(return_value={"answer": "resposta do RAG"})

    with patch(
        "src.application.runtime.dispatcher._transcrever_audio_recebido",
        new_callable=AsyncMock,
        return_value="estou com erro no sistema",
    ), patch(
        "src.application.runtime.dispatcher_langgraph._get_graph",
        new_callable=AsyncMock,
        return_value=fake_app,
    ), patch(
        "src.router.supervisor.rotear", new_callable=AsyncMock
    ) as mock_rotear:
        mock_decision = MagicMock()
        mock_decision.rota = "GERAL"
        mock_rotear.return_value = mock_decision

        result = await processar("", "session-1", user_context)

    # rotear() precisa ter recebido o texto transcrito, não a mensagem vazia original.
    mock_rotear.assert_awaited_once()
    assert mock_rotear.call_args.args[0] == "estou com erro no sistema"
    # E o grafo LangGraph precisa ter sido invocado com o mesmo texto transcrito.
    fake_app.ainvoke.assert_awaited_once()
    invoke_payload = fake_app.ainvoke.call_args.args[0]
    assert invoke_payload["message"] == "estou com erro no sistema"
    assert result.answer == "resposta do RAG"


@pytest.mark.asyncio
async def test_transcricao_falha_retorna_erro_sem_chamar_grafo():
    user_context = {"media_type": "audioMessage", "msg_key_id": "MSG123"}

    with patch(
        "src.application.runtime.dispatcher._transcrever_audio_recebido",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "src.application.runtime.dispatcher_langgraph._get_graph",
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
        "src.application.runtime.dispatcher_langgraph._get_graph",
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
async def test_delegacao_pra_dispatcher_original_recebe_media_type_limpo():
    """Quando a rota classificada não é tratada pelo LangGraph (ex.: SIGAA),
    dispatcher_langgraph delega pra dispatcher.py::processar() original — o
    user_context repassado precisa ter media_type/msg_key_id já limpos, senão
    dispatcher.py tentaria baixar/transcrever o MESMO áudio de novo."""
    user_context = {"media_type": "audioMessage", "msg_key_id": "MSG123"}

    fake_app = MagicMock()
    fake_app.aget_state = AsyncMock(return_value=MagicMock(next=()))

    with patch(
        "src.application.runtime.dispatcher._transcrever_audio_recebido",
        new_callable=AsyncMock,
        return_value="quero consultar meu histórico",
    ), patch(
        "src.application.runtime.dispatcher_langgraph._get_graph",
        new_callable=AsyncMock,
        return_value=fake_app,
    ), patch(
        "src.router.supervisor.rotear", new_callable=AsyncMock
    ) as mock_rotear, patch(
        "src.application.runtime.dispatcher_langgraph._processar_original",
        new_callable=AsyncMock,
    ) as mock_original:
        mock_decision = MagicMock()
        mock_decision.rota = "SIGAA"
        mock_rotear.return_value = mock_decision
        mock_original.return_value = MagicMock()

        await processar("", "session-1", user_context)

    mock_original.assert_awaited_once()
    call_kwargs_or_args = mock_original.call_args
    passed_user_context = call_kwargs_or_args.args[2]
    assert passed_user_context["media_type"] == ""
    assert passed_user_context["msg_key_id"] == ""
    assert call_kwargs_or_args.args[0] == "quero consultar meu histórico"
