"""
tests/unit/orchestration/test_entrypoint.py
===========================================
`src/application/orchestration/entrypoint.py::processar` — o entrypoint único.

ADR 0008 Fase B: classificação e circuit-breaker por agente viraram
`classify_node` (dentro do grafo) — cobertos em `test_classify_node.py`. O
que sobra aqui pro entrypoint testar é o que ele ainda faz de verdade: montar
o payload de mensagem nova sem `route`/`rota` pré-decididos, e ler `rota`/
`plan_id`/`status` de volta do resultado do grafo.
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


def _fake_app(retorno):
    app = MagicMock()
    app.aget_state = AsyncMock(return_value=MagicMock(next=()))
    app.ainvoke = AsyncMock(return_value=retorno)
    return app


@pytest.mark.asyncio
async def test_mensagem_nova_nao_pre_decide_rota():
    """`classify_node` decide `route`/`rota` DENTRO do grafo (Fase B) — o
    entrypoint não escolhe o destino. `route` vai explicitamente vazio (não
    ausente): regressão real — sem isso, `route` de um funil ANTERIOR no
    mesmo `thread_id` sobrevivia no checkpoint e o atalho de REPL de
    `classify_node` (`if state.route:`) tomava esse valor stale como já
    decidido, pulando a classificação numa mensagem nova."""
    app = _fake_app({"answer": "resposta do grafo", "rota": "GERAL"})

    with patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app):
        result = await ep.processar("quando é a matrícula?", "sess-1", {})

    app.ainvoke.assert_awaited_once()
    payload = app.ainvoke.call_args.args[0]
    assert payload["route"] == ""
    assert "rota" not in payload
    assert payload["message"] == "quando é a matrícula?"
    assert payload["cancelado"] is False
    assert result.answer == "resposta do grafo"
    assert result.rota == "GERAL"
    assert result.plan_id == "langgraph_final"


@pytest.mark.asyncio
async def test_resultado_sem_rota_cai_no_fallback_geral():
    """Nunca deveria acontecer (classify_node sempre seta `rota`), mas se o
    grafo devolver sem ela o entrypoint não pode quebrar."""
    app = _fake_app({"answer": "x"})

    with patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app):
        result = await ep.processar("oi", "sess-2", {})

    assert result.rota == "GERAL"


@pytest.mark.asyncio
async def test_plan_id_do_no_de_front_e_respeitado():
    """`classify_node` bloqueado pelo circuit-breaker devolve
    plan_id="agent_disabled" — `_to_os_result` tem que propagar, não
    sobrescrever com "langgraph_final"."""
    app = _fake_app({
        "answer": "🚧 Essa função está temporariamente desativada. Tente novamente mais tarde.",
        "rota": "SIGAA", "status": "ok", "plan_id": "agent_disabled",
    })

    with patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app):
        result = await ep.processar("notas do sigaa", "sess-3", {})

    assert result.plan_id == "agent_disabled"
    assert result.rota == "SIGAA"
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_interrupt_no_resultado_vira_hitl_pending():
    interrupt_mock = MagicMock()
    interrupt_mock.value = {"question": "Qual seu CPF?"}
    app = _fake_app({"__interrupt__": [interrupt_mock], "rota": "TICKET_ABERTURA"})

    with patch("src.application.orchestration.entrypoint._get_graph",
               new_callable=AsyncMock, return_value=app):
        result = await ep.processar("abrir chamado", "sess-4", {})

    assert result.status == "hitl_pending"
    assert result.plan_id == "langgraph_hitl"
    assert result.answer == "Qual seu CPF?"


def test_entrypoint_nao_tem_mais_delegacao_pro_legado():
    """ADR 0008 Fase 3: `dispatcher.py` legado foi deletado — sem regressão
    de trazer de volta um caminho de delegação."""
    assert not hasattr(ep, "_processar_original")
