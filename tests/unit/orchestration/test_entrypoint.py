"""
tests/unit/orchestration/test_entrypoint.py
===========================================
`src/application/orchestration/entrypoint.py::processar` — o entrypoint único.

Fase 1 (ADR 0008): cobre o circuit-breaker por agente que passou a rodar aqui
(antes só em `dispatcher.py`, sem valer pras rotas nativas do grafo).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.application.orchestration.entrypoint as ep


@pytest.fixture(autouse=True)
def _isola_redis_e_rate_limit(monkeypatch):
    async def _sem_sessao(*a, **k):
        return None

    monkeypatch.setattr(
        "src.capabilities.persistence.redis_state.get_hitl_session", _sem_sessao,
    )
    monkeypatch.setattr(
        "src.application.chain.guardrails.InputGuardrail._check_rate_limit",
        lambda self, user_id, r: (False, ""),
    )


@pytest.fixture(autouse=True)
def _reset_graph_singleton():
    ep._graph = None
    yield
    ep._graph = None


def _fake_decision(rota: str):
    from src.router.contracts import RouterDecision

    return RouterDecision(
        rota=rota, confianca=1.0, motivo="teste", cache_hit=False,
        cache_layer="miss", latencia_ms=0, dag_hint={},
    )


def _fake_app():
    app = MagicMock()
    app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    app.ainvoke = AsyncMock(return_value={"answer": "resposta do grafo"})
    return app


@pytest.mark.asyncio
async def test_agente_desativado_bloqueia_rota_nativa(monkeypatch):
    """GERAL (owner=langgraph, agente=academic_knowledge) — desligar o agente
    em /hub/agents agora bloqueia mesmo as rotas nativas do grafo."""
    app = _fake_app()

    async def _desativado(_r, nome):
        assert nome == "academic_knowledge"
        return False

    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app), \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled", _desativado):
        mock_rotear.return_value = _fake_decision("GERAL")
        result = await ep.processar("quando é a matrícula?", "sess-cb-1", {})

    assert result.plan_id == "agent_disabled"
    assert result.status == "ok"
    assert "desativada" in result.answer.lower()
    app.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_agente_ativo_segue_pro_grafo(monkeypatch):
    app = _fake_app()

    async def _ativo(_r, _nome):
        return True

    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app), \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled", _ativo):
        mock_rotear.return_value = _fake_decision("GERAL")
        result = await ep.processar("quando é a matrícula?", "sess-cb-2", {})

    app.ainvoke.assert_awaited_once()
    assert result.answer == "resposta do grafo"


@pytest.mark.asyncio
async def test_rota_sem_agente_nao_checa_breaker(monkeypatch):
    """GREETING tem agente=NULL — não deve nem tentar checar o breaker."""
    from src.infrastructure import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "FEATURE_LANGGRAPH_NATIVE_ROUTES", True)
    app = _fake_app()
    sentinel = MagicMock(side_effect=AssertionError("breaker não deve ser consultado"))

    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app), \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled", sentinel):
        mock_rotear.return_value = _fake_decision("GREETING")
        await ep.processar("oi", "sess-cb-3", {"chat_id": "x@s.whatsapp.net"})

    sentinel.assert_not_called()
    app.ainvoke.assert_awaited_once()
