# tests/unit/application/test_langgraph_native_routes_flag.py
"""
Fase 2d (Decisão 01): dispatcher_langgraph.py::processar() só roteia
CHECK_STATUS/GREETING/MEDIA_DOWNLOAD/SIGAA pro grafo quando
settings.FEATURE_LANGGRAPH_NATIVE_ROUTES está ligada. Desligada (default),
comportamento idêntico a antes da Fase 2d: delega inteiro pro
dispatcher.py original.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.application.runtime.dispatcher_langgraph as dlg


@pytest.fixture(autouse=True)
def _sem_redis_hitl_legado(monkeypatch):
    async def _sem_sessao(*a, **k):
        return None

    monkeypatch.setattr(
        "src.capabilities.persistence.redis_state.get_hitl_session", _sem_sessao,
    )


@pytest.fixture(autouse=True)
def _reset_graph_singleton():
    dlg._graph = None
    yield
    dlg._graph = None


def _fake_decision(rota: str):
    from src.router.contracts import RouterDecision

    return RouterDecision(
        rota=rota, confianca=1.0, motivo="teste", cache_hit=False,
        cache_layer="miss", latencia_ms=0, dag_hint={},
    )


@pytest.mark.parametrize("rota", ["CHECK_STATUS", "GREETING", "MEDIA_DOWNLOAD", "SIGAA"])
@pytest.mark.asyncio
async def test_flag_desligada_ainda_delega_pro_dispatcher_original(monkeypatch, rota):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "FEATURE_LANGGRAPH_NATIVE_ROUTES", False)

    # _get_graph()/aget_state() rodam SEMPRE, antes da classificação — pra
    # checar se há um interrupt() pendente (funil ticket/crud). Precisa
    # devolver next=() (sem funil pendente) pra chegar até o roteamento.
    fake_app = MagicMock()
    fake_app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    fake_app.ainvoke = AsyncMock()

    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch(
             "src.application.runtime.dispatcher_langgraph._processar_original",
             new_callable=AsyncMock,
         ) as mock_original, \
         patch(
             "src.application.runtime.dispatcher_langgraph._get_graph",
             new_callable=AsyncMock, return_value=fake_app,
         ):
        mock_rotear.return_value = _fake_decision(rota)
        mock_original.return_value = MagicMock()

        await dlg.processar("mensagem qualquer", "sess-1", {})

    mock_original.assert_awaited_once()
    fake_app.ainvoke.assert_not_called()  # nunca chega a invocar o grafo


@pytest.mark.parametrize(
    "rota,node_name",
    [
        ("CHECK_STATUS", "check_status"),
        ("GREETING", "greeting"),
        ("MEDIA_DOWNLOAD", "media_download"),
        ("SIGAA", "sigaa"),
    ],
)
@pytest.mark.asyncio
async def test_flag_ligada_roteia_pro_grafo(monkeypatch, rota, node_name):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "FEATURE_LANGGRAPH_NATIVE_ROUTES", True)

    fake_app = MagicMock()
    fake_app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    fake_app.ainvoke = AsyncMock(return_value={"answer": f"resposta de {node_name}"})

    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch(
             "src.application.runtime.dispatcher_langgraph._processar_original",
             new_callable=AsyncMock,
         ) as mock_original, \
         patch(
             "src.application.runtime.dispatcher_langgraph._get_graph",
             new_callable=AsyncMock, return_value=fake_app,
         ):
        mock_rotear.return_value = _fake_decision(rota)

        result = await dlg.processar("mensagem qualquer", "sess-1", {"chat_id": "5598@s.whatsapp.net"})

    mock_original.assert_not_called()
    fake_app.ainvoke.assert_awaited_once()
    payload = fake_app.ainvoke.call_args.args[0]
    assert payload["route"] == node_name
    assert payload["user_context"] == {"chat_id": "5598@s.whatsapp.net"}
    assert result.answer == f"resposta de {node_name}"
    assert result.rota == rota
