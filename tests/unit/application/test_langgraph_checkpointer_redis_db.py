# tests/unit/application/test_langgraph_checkpointer_redis_db.py
"""
Fase 2c do plano de integração (Decisão 04): o checkpointer AsyncRedisSaver
do LangGraph deve usar uma DB Redis dedicada (índice /3), separada da DB/0
de dados de negócio — antes usava settings.REDIS_URL direto (DB/0, mesma
dos dados reais), sem isolamento consciente.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.application.runtime.dispatcher_langgraph as dlg


@pytest.fixture(autouse=True)
def _reset_graph_singleton():
    dlg._graph = None
    dlg._saver_cm = None
    yield
    dlg._graph = None
    dlg._saver_cm = None


@pytest.mark.asyncio
async def test_get_graph_usa_db_redis_dedicada_pro_checkpointer(monkeypatch):
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "REDIS_URL", "redis://redis:6379/0")

    saver_mock = MagicMock()
    saver_mock.asetup = AsyncMock(return_value=None)

    saver_cm = MagicMock()
    saver_cm.__aenter__ = AsyncMock(return_value=saver_mock)

    async_redis_saver_cls = MagicMock()
    async_redis_saver_cls.from_conn_string = MagicMock(return_value=saver_cm)

    with patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", async_redis_saver_cls), \
         patch("langgraph_experiment.graph.build_graph", return_value="grafo-fake"):
        graph = await dlg._get_graph()

    assert graph == "grafo-fake"
    async_redis_saver_cls.from_conn_string.assert_called_once_with("redis://redis:6379/3")
