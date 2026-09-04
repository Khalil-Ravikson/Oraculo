"""
tests/unit/orchestration/test_classify_node.py
==============================================
`orchestration/nodes.py::classify_node` (ADR 0008 Fase B) — passou a
classificar de verdade (Supervisor real) e aplicar o circuit-breaker por
agente, ambos antes vivendo em `entrypoint.py::processar()` como Python puro
ANTES do grafo. Cobre exatamente o comportamento que a Fase 1/3 já garantiam
por lá, agora como nó.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.application.orchestration.nodes import classify_node
from src.application.orchestration.state import OraculoState


def _fake_decision(rota: str):
    from src.router.contracts import RouterDecision
    return RouterDecision(
        rota=rota, confianca=1.0, motivo="teste", cache_hit=False,
        cache_layer="miss", latencia_ms=0, dag_hint={},
    )


@pytest.mark.asyncio
async def test_classify_node_com_route_preenchido_e_passthrough():
    """Só pro REPL — em produção `state.route` nunca chega preenchido aqui."""
    state = OraculoState(session_id="s1", message="oi", route="rag")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear:
        result = await classify_node(state)
    mock_rotear.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_classify_node_classifica_e_seta_rota_e_route():
    state = OraculoState(session_id="s1", message="quando é a matrícula?")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision("GERAL")
        result = await classify_node(state)

    mock_rotear.assert_awaited_once_with("quando é a matrícula?", "s1", {})
    assert result["rota"] == "GERAL"
    assert result["route"] == "rag"
    assert result["cancelado"] is False


@pytest.mark.asyncio
async def test_classify_node_reseta_ticket_data_ao_iniciar_funil_novo():
    """ATENÇÃO 2 (entrypoint.py): sem esse reset, ticket_data de uma execução
    anterior no mesmo thread_id vazaria pro funil novo."""
    state = OraculoState(
        session_id="s1", message="quero abrir um chamado",
        ticket_data={"tipo": "incidente", "categoria": "wifi"}, ticket_error="algo",
        ticket_confirmed=True, cancelado=True,
    )
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision("TICKET_ABERTURA")
        result = await classify_node(state)

    assert result["route"] == "ticket"
    assert result["ticket_data"] == {}
    assert result["ticket_error"] == ""
    assert result["ticket_confirmed"] is None
    assert result["cancelado"] is False  # sempre resetado, independente da rota


@pytest.mark.asyncio
async def test_classify_node_reseta_crud_data_ao_iniciar_funil_novo():
    state = OraculoState(
        session_id="s1", message="quero atualizar meu telefone",
        crud_data={"campo": "setor", "valor": "PROG"}, crud_confirmed=False,
    )
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision("CRUD")
        result = await classify_node(state)

    assert result["route"] == "crud"
    assert result["crud_data"] == {}
    assert result["crud_confirmed"] is None


@pytest.mark.asyncio
async def test_classify_node_nao_reseta_ticket_data_pra_rota_rag():
    """Só reseta o funil que está sendo INICIADO — o outro nem aparece no
    delta (LangGraph só sobrescreve as chaves devolvidas)."""
    state = OraculoState(session_id="s1", message="qual o calendário?")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision("CALENDARIO")
        result = await classify_node(state)

    assert result["route"] == "rag"
    assert "ticket_data" not in result
    assert "crud_data" not in result


@pytest.mark.asyncio
async def test_classify_node_circuit_breaker_bloqueia_agente_desativado():
    state = OraculoState(session_id="s1", message="notas do sigaa")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=False) as mock_enabled:
        mock_rotear.return_value = _fake_decision("SIGAA")
        result = await classify_node(state)

    mock_enabled.assert_awaited_once()
    assert mock_enabled.call_args.args[1] == "sigaa"
    assert result["early_exit"] is True
    assert result["plan_id"] == "agent_disabled"
    assert result["status"] == "ok"
    assert result["rota"] == "SIGAA"
    assert "desativada" in result["answer"].lower()
    assert "route" not in result  # não decidiu destino — bloqueou antes


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
async def test_classify_node_rotas_ex_condicionais_roteiam_pro_no_certo(rota, node_name):
    """ADR 0008 Fase 3: essas 4 rotas só rodavam nativas com uma flag ligada
    (deletada); agora vão sempre pro nó certo, decidido aqui como qualquer
    outra rota — sem caminho especial."""
    state = OraculoState(session_id="s1", message="mensagem qualquer")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision(rota)
        result = await classify_node(state)

    assert result["route"] == node_name
    assert result["rota"] == rota


@pytest.mark.asyncio
async def test_classify_node_rota_sem_agente_nao_checa_breaker():
    """GREETING/MEDIA_DOWNLOAD/CHECK_STATUS/ESCALAR_HUMANO têm agente=NULL —
    não devem nem tentar consultar o circuit-breaker."""
    state = OraculoState(session_id="s1", message="oi")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock) as mock_enabled:
        mock_rotear.return_value = _fake_decision("GREETING")
        result = await classify_node(state)

    mock_enabled.assert_not_called()
    assert result["route"] == "greeting"


@pytest.mark.asyncio
async def test_classify_node_entrypoint_node_invalido_cai_em_rag(monkeypatch):
    """Rede de segurança: rota classificada aponta pra um entrypoint_node que
    não é destino do fan-out na spec ATIVA — checado contra
    `builder.route_values_ativos()`."""
    from src.application.orchestration import builder

    monkeypatch.setattr(builder, "_ULTIMO_ROUTE_VALUES", frozenset({"rag", "greeting"}))
    state = OraculoState(session_id="s1", message="algo customizado")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.infrastructure.route_registry.get") as mock_get:
        from src.infrastructure.route_registry import RouteConfig
        mock_rotear.return_value = _fake_decision("ROTA_CUSTOM_QUEBRADA")
        mock_get.return_value = RouteConfig(
            rota="ROTA_CUSTOM_QUEBRADA", entrypoint_node="no_removido", owner="langgraph",
            agente=None, cacheavel=False, permite_detour=False, doc_type=None, k=0,
        )
        result = await classify_node(state)

    assert result["route"] == "rag"


@pytest.mark.asyncio
async def test_classify_node_ticket_e_crud_nao_sao_falso_positivo_da_rede_de_seguranca(monkeypatch):
    """Regressão: `route_value` ("ticket"/"crud") e id de nó do grafo
    ("ticket_ask_tipo"/"crud_ask_campo") são namespaces DIFERENTES — comparar
    entrypoint_node contra ids de nó rejeitaria essas duas rotas sempre.
    `route_values_ativos()` (não `node_ids`) é a fonte certa."""
    from src.application.orchestration import builder

    monkeypatch.setattr(
        builder, "_ULTIMO_ROUTE_VALUES",
        frozenset({"rag", "ticket", "crud", "greeting", "sigaa", "media_download",
                   "check_status", "human_handoff"}),
    )
    state = OraculoState(session_id="s1", message="quero abrir um chamado")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision("TICKET_ABERTURA")
        result = await classify_node(state)

    assert result["route"] == "ticket"


@pytest.mark.asyncio
async def test_classify_node_sem_route_values_ativos_nao_bloqueia(monkeypatch):
    """Sem `route_values_ativos()` populado ainda (processo acabou de subir,
    nunca compilou o grafo real) a checagem fica leniente — nunca derruba a
    rota por falta de dado."""
    from src.application.orchestration import builder

    monkeypatch.setattr(builder, "_ULTIMO_ROUTE_VALUES", frozenset())
    state = OraculoState(session_id="s1", message="oi")
    with patch("src.router.supervisor.rotear", new_callable=AsyncMock) as mock_rotear, \
         patch("src.capabilities.persistence.agent_config.is_agent_enabled",
               new_callable=AsyncMock, return_value=True):
        mock_rotear.return_value = _fake_decision("GERAL")
        result = await classify_node(state)

    assert result["route"] == "rag"
