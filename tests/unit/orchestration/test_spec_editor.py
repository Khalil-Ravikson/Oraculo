"""
`orchestration/spec_editor.py` — as edições de alto nível que a GUI do Graph
Studio faz na `GraphSpec` (ADR 0008 Fase 4).
"""
from __future__ import annotations

import pytest

from src.application.orchestration import spec_editor
from src.application.orchestration.loader import default_spec
from src.application.orchestration.spec import GraphSpec


def test_node_id_de_rota():
    assert spec_editor.node_id_de_rota("FAQ") == "faq"
    assert spec_editor.node_id_de_rota("NOTAS SIGAA") == "notas_sigaa"
    assert spec_editor.node_id_de_rota("  Bolsa-PIBIC ") == "bolsa_pibic"


def test_adicionar_rota_produz_spec_valida():
    raw = spec_editor.adicionar_rota(
        default_spec(), node_id="faq", node_type="rag", config={"doc_type": "wiki_ctic", "k": 8},
    )
    nova = GraphSpec.model_validate(raw)
    assert nova.validate_topology() == []
    assert any(n.id == "faq" for n in nova.nodes)
    assert any(e.source == "classify" and e.route_value == "faq" for e in nova.edges)
    assert any(e.source == "faq" and e.target == "__end__" for e in nova.edges)


def test_adicionar_rota_rejeita_tipo_nao_adicionavel():
    with pytest.raises(spec_editor.EdicaoInvalida, match="não pode ser adicionado"):
        spec_editor.adicionar_rota(default_spec(), node_id="x", node_type="ticket_ask_tipo")


def test_adicionar_rota_rejeita_id_colidindo():
    with pytest.raises(spec_editor.EdicaoInvalida, match="já existe"):
        spec_editor.adicionar_rota(default_spec(), node_id="rag", node_type="rag")


def test_adicionar_rota_rejeita_id_invalido():
    with pytest.raises(spec_editor.EdicaoInvalida):
        spec_editor.adicionar_rota(default_spec(), node_id="Faq!", node_type="rag")


def test_remover_rota_desfaz_o_add():
    raw = spec_editor.adicionar_rota(default_spec(), node_id="faq", node_type="greeting")
    spec_com_faq = GraphSpec.model_validate(raw)
    raw2 = spec_editor.remover_rota(spec_com_faq, "faq")
    nova = GraphSpec.model_validate(raw2)
    assert nova.validate_topology() == []
    assert nova.model_dump() == default_spec().model_dump()


def test_remover_rota_rejeita_no_travado():
    with pytest.raises(spec_editor.EdicaoInvalida, match="esqueleto"):
        spec_editor.remover_rota(default_spec(), "ticket_ask_tipo")


def test_rotas_editaveis_lista_so_as_nao_travadas():
    assert spec_editor.rotas_editaveis(default_spec()) == []
    raw = spec_editor.adicionar_rota(default_spec(), node_id="faq", node_type="rag", config={"doc_type": "x", "k": 5})
    editaveis = spec_editor.rotas_editaveis(GraphSpec.model_validate(raw))
    assert len(editaveis) == 1
    assert editaveis[0]["node_id"] == "faq" and editaveis[0]["node_type"] == "rag"


def test_criar_rota_greeting_nao_esbarra_na_validacao_de_entrypoint():
    """Regressão: o endpoint /nova-rota adiciona o nó na spec E cria a linha
    de route_registry na mesma transação. `validar_campos` NÃO pode reprovar
    o `entrypoint_node` só porque o nó ainda não está na spec ATIVA — a
    topologia com ele já foi validada por `adicionar_rota`."""
    from src.infrastructure import route_registry

    node_id = spec_editor.node_id_de_rota("TESTE")
    raw = spec_editor.adicionar_rota(default_spec(), node_id=node_id, node_type="greeting", config={})
    assert node_id in {n["id"] for n in raw["nodes"]}

    # o endpoint valida os campos SEM entrypoint_node e injeta depois
    campos = route_registry.validar_campos({
        "owner": "langgraph", "agente": None, "cacheavel": True,
        "permite_detour": False, "doc_type": None, "k": 0,
    })
    campos["entrypoint_node"] = node_id
    assert campos["entrypoint_node"] == "teste"
