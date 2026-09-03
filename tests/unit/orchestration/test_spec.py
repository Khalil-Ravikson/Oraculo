"""
tests/unit/orchestration/test_spec.py
=====================================
`GraphSpec.validate_topology()` — a rede de segurança que roda no POST do Hub
antes de gravar uma topologia nova (ADR 0008 Fase 5).
"""
from __future__ import annotations

import pytest

from src.application.orchestration.loader import default_spec
from src.application.orchestration.spec import GraphSpec, spec_valida_ou_erro


def _spec_minima(**over) -> dict:
    base = {
        "version": 1,
        "entrypoint": "classify",
        "nodes": [
            {"id": "classify", "type": "classify"},
            {"id": "rag", "type": "rag"},
        ],
        "edges": [
            {"source": "classify", "when": "by_state_route", "route_value": "rag", "target": "rag"},
            {"source": "rag", "target": "__end__"},
        ],
    }
    base.update(over)
    return base


def test_spec_minima_valida():
    assert GraphSpec.model_validate(_spec_minima()).validate_topology() == []


def test_default_json_valido():
    assert default_spec().validate_topology() == []


def test_rejeita_tipo_inexistente():
    raw = _spec_minima(nodes=[
        {"id": "classify", "type": "classify"},
        {"id": "x", "type": "tipo_que_nao_existe"},
    ], edges=[
        {"source": "classify", "when": "by_state_route", "route_value": "x", "target": "x"},
        {"source": "x", "target": "__end__"},
    ])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("tipo_que_nao_existe" in e for e in erros)


def test_rejeita_target_desconhecido():
    raw = _spec_minima(edges=[
        {"source": "classify", "when": "by_state_route", "route_value": "rag", "target": "fantasma"},
        {"source": "rag", "target": "__end__"},
    ])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("target desconhecido" in e for e in erros)


def test_rejeita_router_inexistente():
    raw = _spec_minima(edges=[
        {"source": "classify", "when": "router_fantasma", "route_value": "rag", "target": "rag"},
        {"source": "rag", "target": "__end__"},
    ])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("router_fantasma" in e for e in erros)


def test_rejeita_no_inalcancavel():
    raw = _spec_minima(nodes=[
        {"id": "classify", "type": "classify"},
        {"id": "rag", "type": "rag"},
        {"id": "orfao", "type": "greeting"},
    ], edges=[
        {"source": "classify", "when": "by_state_route", "route_value": "rag", "target": "rag"},
        {"source": "rag", "target": "__end__"},
        {"source": "orfao", "target": "__end__"},
    ])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("inalcanç" in e.lower() or "inalcanc" in e.lower() for e in erros)


def test_rejeita_no_sem_caminho_ate_o_fim():
    raw = _spec_minima(nodes=[
        {"id": "classify", "type": "classify"},
        {"id": "rag", "type": "rag"},
        {"id": "greeting", "type": "greeting"},
    ], edges=[
        {"source": "classify", "when": "by_state_route", "route_value": "rag", "target": "rag"},
        {"source": "classify", "when": "by_state_route", "route_value": "greeting", "target": "greeting"},
        {"source": "rag", "target": "__end__"},
        {"source": "greeting", "target": "greeting"},  # loop infinito, nunca chega a __end__
    ])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("caminho até o fim" in e for e in erros)


def test_rejeita_ids_duplicados():
    raw = _spec_minima(nodes=[
        {"id": "rag", "type": "classify"},
        {"id": "rag", "type": "rag"},
    ], entrypoint="rag", edges=[{"source": "rag", "target": "__end__"}])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("duplicad" in e for e in erros)


def test_rejeita_mistura_condicional_e_simples_no_mesmo_no():
    raw = _spec_minima(edges=[
        {"source": "classify", "when": "by_state_route", "route_value": "rag", "target": "rag"},
        {"source": "classify", "target": "rag"},
        {"source": "rag", "target": "__end__"},
    ])
    erros = GraphSpec.model_validate(raw).validate_topology()
    assert any("mistura" in e for e in erros)


def test_spec_valida_ou_erro_levanta_com_lista():
    with pytest.raises(ValueError, match="GraphSpec inválida"):
        spec_valida_ou_erro(_spec_minima(entrypoint="nao_existe"))


def test_adicionar_rota_nova_e_valido():
    """O caso de uso da GUI: uma rota FAQ nova = 1 nó `faq` (tipo `rag`) +
    aresta classify->faq + aresta faq->__end__."""
    raw = default_spec().model_dump()
    raw["nodes"].append({"id": "faq", "type": "rag", "config": {"doc_type": "wiki_ctic"}})
    raw["edges"].append(
        {"source": "classify", "when": "by_state_route", "route_value": "faq", "target": "faq"}
    )
    raw["edges"].append({"source": "faq", "target": "__end__"})
    assert GraphSpec.model_validate(raw).validate_topology() == []
