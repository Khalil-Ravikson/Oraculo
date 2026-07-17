# tests/unit/application/test_dispatcher_circuit_breaker.py
"""
Regressão: `dispatcher.py::processar()` chamava `is_agent_enabled(...)` sem
`await` depois que a Sprint 2 (Fase 6) tornou essa função async. O bug foi
descoberto num teste manual real via docker-compose ("RuntimeWarning:
coroutine 'is_agent_enabled' was never awaited") — `not <coroutine>` é
sempre False, então o circuit-breaker por agente (liga/desliga em
/hub/agents) nunca desativava nada de verdade.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.runtime.dispatcher import processar


class MockRedis:
    def __init__(self):
        self.db = {}
    def get(self, key):
        return self.db.get(key)
    def setex(self, key, ttl, value):
        self.db[key] = value
    def exists(self, key):
        return key in self.db
    def delete(self, key):
        self.db.pop(key, None)
    def xadd(self, name, fields, **kwargs):
        pass


@pytest.mark.asyncio
async def test_circuit_breaker_desativa_agente_de_fato():
    mock_redis = MockRedis()

    mock_decision = MagicMock()
    mock_decision.rota = "WIKI"
    mock_decision.cache_hit = False
    mock_decision.dag_hint = {}

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("src.agents.sigaa.auth_flow.handle_hitl_continuation", new=AsyncMock(return_value=None)), \
         patch("src.router.supervisor.rotear", new=AsyncMock(return_value=mock_decision)), \
         patch(
             "src.capabilities.persistence.agent_config.is_agent_enabled",
             new=AsyncMock(return_value=False),
         ):
        result = await processar("!wiki ctic", "session-1", {"role": "student"})

    assert result.plan_id == "agent_disabled"
    assert "desativada" in result.answer.lower()


@pytest.mark.asyncio
async def test_circuit_breaker_deixa_passar_agente_ativo():
    mock_redis = MockRedis()

    mock_decision = MagicMock()
    mock_decision.rota = "WIKI"
    mock_decision.cache_hit = True  # atalho: evita precisar mockar todo o pipeline de dispatch

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("src.agents.sigaa.auth_flow.handle_hitl_continuation", new=AsyncMock(return_value=None)), \
         patch("src.router.supervisor.rotear", new=AsyncMock(return_value=mock_decision)), \
         patch(
             "src.capabilities.persistence.agent_config.is_agent_enabled",
             new=AsyncMock(return_value=True),
         ):
        result = await processar("!wiki ctic", "session-1", {"role": "student"})

    assert result.plan_id != "agent_disabled"
