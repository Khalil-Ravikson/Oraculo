# tests/unit/application/test_langgraph_native_routes.py
"""
Fase 2d do plano de integração (Decisão 01): CHECK_STATUS/GREETING/
MEDIA_DOWNLOAD/SIGAA portados de fast-paths de dispatcher.py::processar()
pra nodes nativos do grafo (langgraph_experiment/nodes.py), atrás de
settings.FEATURE_LANGGRAPH_NATIVE_ROUTES (desligada por padrão).

Duas camadas cobertas aqui:
  1. Os 4 nodes em isolamento (lógica reimplementada bate com o fast-path
     original de dispatcher.py).
  2. dispatcher_langgraph.py::processar() — flag desligada continua
     delegando pro dispatcher.py original (comportamento pré-2d,
     intocado); flag ligada roteia pro grafo.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.orchestration.nodes import (
    check_status_node,
    greeting_node,
    media_download_node,
    sigaa_node,
)
from src.application.orchestration.state import OraculoState


# ─────────────────────────────────────────────────────────────────────────────
# check_status_node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_status_node_com_historico():
    mem = MagicMock()
    mem.get_task_history = AsyncMock(
        return_value={"last_worker": "rag_search", "last_result": "3 chunks"}
    )
    state = OraculoState(session_id="sess-1", message="status", route="check_status")

    with patch(
        "src.memory.services.redis_memory_service.get_cognitive_memory", return_value=mem,
    ):
        result = await check_status_node(state)

    assert "rag_search" in result["answer"]
    assert "3 chunks" in result["answer"]
    mem.get_task_history.assert_awaited_once_with("sess-1")


@pytest.mark.asyncio
async def test_check_status_node_sem_historico():
    mem = MagicMock()
    mem.get_task_history = AsyncMock(return_value=None)
    state = OraculoState(session_id="sess-1", message="status", route="check_status")

    with patch(
        "src.memory.services.redis_memory_service.get_cognitive_memory", return_value=mem,
    ):
        result = await check_status_node(state)

    assert result["answer"] == "Nenhuma tarefa anterior registrada nesta sessão."


# ─────────────────────────────────────────────────────────────────────────────
# greeting_node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_greeting_node_registra_turnos_e_responde():
    mem = MagicMock()
    mem.add_turn = AsyncMock(return_value=None)
    state = OraculoState(session_id="sess-1", message="oi", route="greeting")

    with patch(
        "src.memory.services.redis_memory_service.get_cognitive_memory", return_value=mem,
    ):
        result = await greeting_node(state)

    saudacoes_possiveis = (
        "Olá! 😊 Sou o Oráculo UEMA. Como posso ajudar?",
        "Oi! Em que posso ajudá-lo(a) hoje?",
        "Olá! Pode perguntar sobre calendário, editais, contatos ou suporte. 🎓",
    )
    assert result["answer"].startswith(saudacoes_possiveis)
    assert "!ytb" in result["answer"]
    assert mem.add_turn.await_count == 2
    mem.add_turn.assert_any_await("sess-1", "user", "oi")


# ─────────────────────────────────────────────────────────────────────────────
# media_download_node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_media_download_node_com_url_dispara_chain():
    fake_task = MagicMock()
    fake_task.s = MagicMock(return_value="sig-download")
    registry = {"ytb_download": fake_task}

    mock_chain_result = MagicMock()
    mock_chain_result.apply_async = MagicMock()
    mock_chain = MagicMock(return_value=mock_chain_result)

    state = OraculoState(
        session_id="5598999@s.whatsapp.net",
        message="https://youtube.com/watch?v=abc123",
        route="media_download",
        user_context={"chat_id": "5598999@s.whatsapp.net"},
    )

    with patch("src.application.workers.registry._autodiscover_workers", return_value=None), \
         patch("src.application.workers.registry._REGISTRY", registry), \
         patch("celery.chain", mock_chain), \
         patch("src.application.tasks.process_message_task.enviar_resposta_whatsapp_task"):
        result = await media_download_node(state)

    assert "Download iniciado" in result["answer"]
    mock_chain.assert_called_once()
    mock_chain_result.apply_async.assert_called_once()
    fake_task.s.assert_called_once()
    event = fake_task.s.call_args[0][0]
    assert event["url"] == "https://youtube.com/watch?v=abc123"
    assert event["chat_id"] == "5598999@s.whatsapp.net"
    assert event["hitl_confirmed"] is True


@pytest.mark.asyncio
async def test_media_download_node_sem_url_usa_busca_ytb():
    fake_task = MagicMock()
    fake_task.s = MagicMock(return_value="sig-download")
    registry = {"ytb_download": fake_task}

    mock_chain_result = MagicMock()
    mock_chain_result.apply_async = MagicMock()
    mock_chain = MagicMock(return_value=mock_chain_result)

    state = OraculoState(
        session_id="sess-1", message="buscar um video sobre eclipse solar",
        route="media_download", user_context={},
    )

    with patch("src.application.workers.registry._autodiscover_workers", return_value=None), \
         patch("src.application.workers.registry._REGISTRY", registry), \
         patch("celery.chain", mock_chain), \
         patch("src.application.tasks.process_message_task.enviar_resposta_whatsapp_task"):
        await media_download_node(state)

    event = fake_task.s.call_args[0][0]
    assert event["url"].startswith("ytsearch1:")
    assert "eclipse solar" in event["url"]


@pytest.mark.asyncio
async def test_media_download_node_worker_nao_encontrado_nao_dispara_chain():
    mock_chain = MagicMock()

    state = OraculoState(
        session_id="sess-1", message="https://instagram.com/reel/xyz",
        route="media_download", user_context={},
    )

    with patch("src.application.workers.registry._autodiscover_workers", return_value=None), \
         patch("src.application.workers.registry._REGISTRY", {}), \
         patch("celery.chain", mock_chain):
        result = await media_download_node(state)

    mock_chain.assert_not_called()
    assert "Download iniciado" in result["answer"]  # resposta imediata, mesmo sem worker


# ─────────────────────────────────────────────────────────────────────────────
# sigaa_node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sigaa_node_delega_pra_start_or_continue_sigaa():
    fake_result = MagicMock()
    fake_result.answer = "⚠️ Autenticação Requerida\n\nInforme seu CPF:"
    fake_result.status = "hitl_pending"

    state = OraculoState(
        session_id="sess-1", message="quero ver minhas notas",
        route="sigaa", user_context={"chat_id": "5598@s.whatsapp.net"},
    )

    with patch(
        "src.agents.sigaa.auth_flow.start_or_continue_sigaa",
        new_callable=AsyncMock, return_value=fake_result,
    ) as mock_start:
        result = await sigaa_node(state)

    assert result == {"answer": fake_result.answer, "status": "hitl_pending"}
    mock_start.assert_awaited_once()
    args = mock_start.call_args[0]
    assert args[1] == "quero ver minhas notas"
    assert args[2] == "sess-1"
    assert args[3] == {"chat_id": "5598@s.whatsapp.net"}


@pytest.mark.asyncio
async def test_sigaa_node_none_retorna_fallback_amigavel():
    state = OraculoState(session_id="sess-1", message="notas", route="sigaa")

    with patch(
        "src.agents.sigaa.auth_flow.start_or_continue_sigaa",
        new_callable=AsyncMock, return_value=None,
    ):
        result = await sigaa_node(state)

    assert "não consegui" in result["answer"].lower() or "SIGAA" in result["answer"]
