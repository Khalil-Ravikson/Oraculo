# tests/unit/application/test_langgraph_native_routes_flag.py
"""
ADR 0008 Fase 3: o `dispatcher.py` legado foi deletado. CHECK_STATUS /
GREETING / MEDIA_DOWNLOAD / SIGAA (que antes só rodavam no grafo com
`FEATURE_LANGGRAPH_NATIVE_ROUTES` ligada) agora vão SEMPRE pro grafo, como
todas as outras rotas. Não existe mais caminho de delegação.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.application.orchestration.entrypoint as ep


@pytest.fixture(autouse=True)
def _sem_redis_hitl_legado(monkeypatch):
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
async def test_rotas_ex_condicionais_vao_sempre_pro_grafo(monkeypatch, rota, node_name):
    fake_app = MagicMock()
    fake_app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    fake_app.ainvoke = AsyncMock(return_value={"answer": f"resposta de {node_name}"})

    async def _ativo(_r, _nome):
        return True

    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled", _ativo), \
         patch(
             "src.application.orchestration.entrypoint._get_graph",
             new_callable=AsyncMock, return_value=fake_app,
         ):
        mock_rotear.return_value = _fake_decision(rota)

        result = await ep.processar(
            "mensagem qualquer", "sess-1", {"chat_id": "5598@s.whatsapp.net"},
        )

    fake_app.ainvoke.assert_awaited_once()
    payload = fake_app.ainvoke.call_args.args[0]
    assert payload["route"] == node_name
    assert payload["user_context"] == {"chat_id": "5598@s.whatsapp.net"}
    assert result.answer == f"resposta de {node_name}"
    assert result.rota == rota


@pytest.mark.asyncio
async def test_entrypoint_nao_tem_mais_delegacao_pro_legado():
    assert not hasattr(ep, "_processar_original")
