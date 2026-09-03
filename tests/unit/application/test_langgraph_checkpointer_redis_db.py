# tests/unit/application/test_langgraph_checkpointer_redis_db.py
"""
Reversão da Decisão 04 (Fase 2c): o checkpointer AsyncRedisSaver do
LangGraph precisa ficar na MESMA DB/0 dos dados de negócio (RAG), não numa
DB isolada (/3) como a Decisão 04 tentou. Motivo: AsyncRedisSaver.asetup()
cria um índice RediSearch (FT.CREATE) para os checkpoints, e o módulo
RediSearch só cria índice na DB/0 — usar /3 quebra com "Failed to create
index 'checkpoint' on Redis: Cannot create index on db != 0" (erro do
servidor, reproduzido em produção). Sem colisão funcional com o RAG porque
os prefixos de chave já são distintos (checkpoint:* vs rag:chunk:*/
tools:emb:*). Celery (broker /1, result backend /2) não é afetado porque
não usa RediSearch.
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
async def test_get_graph_usa_mesma_db_do_redis_url_pro_checkpointer(monkeypatch):
    """AsyncRedisSaver precisa da DB/0 (RediSearch só indexa lá) — mesma DB
    usada por settings.REDIS_URL, sem rebind pra /3."""
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "REDIS_URL", "redis://redis:6379/0")

    saver_mock = MagicMock()
    saver_mock.asetup = AsyncMock(return_value=None)

    saver_cm = MagicMock()
    saver_cm.__aenter__ = AsyncMock(return_value=saver_mock)

    async_redis_saver_cls = MagicMock()
    async_redis_saver_cls.from_conn_string = MagicMock(return_value=saver_cm)

    with patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", async_redis_saver_cls), \
         patch("src.application.orchestration.builder.build_graph", return_value="grafo-fake"):
        graph = await dlg._get_graph()

    assert graph == "grafo-fake"
    async_redis_saver_cls.from_conn_string.assert_called_once_with("redis://redis:6379/0")
