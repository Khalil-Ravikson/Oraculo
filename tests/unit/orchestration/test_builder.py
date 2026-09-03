"""
tests/unit/orchestration/test_builder.py
========================================
Trava a topologia do grafo de orquestração (`src/application/orchestration/
builder.py`).

Este teste congela o conjunto de nós e arestas do grafo de orquestração —
rede de segurança contra mudanças acidentais de topologia. Toda alteração
desta baseline deve ser DELIBERADA, num commit que também explica a mudança.

Histórico:
- Fase 0: baseline igual a `langgraph_experiment/graph.py`.
- Fase 2: + nó `human_handoff` (rota ESCALAR_HUMANO) e suas 2 arestas.
- Fase 5: topologia vem de `GraphSpec` (`specs/default.json`). O
  `test_spec_default_equivale_a_baseline` prova que a spec produz o MESMO
  grafo compilado que a baseline congelada aqui.
"""
from __future__ import annotations

from src.application.orchestration.builder import build_graph, build_graph_from_spec
from src.application.orchestration.loader import default_spec

NODES_ESPERADOS = {
    "__start__", "__end__",
    "classify", "rag", "check_status", "greeting", "media_download", "sigaa",
    "human_handoff",
    "ticket_ask_tipo", "ticket_ask_categoria", "ticket_ask_queixa",
    "ticket_confirm", "ticket_save",
    "crud_ask_campo", "crud_ask_valor", "crud_confirm", "crud_save",
}

EDGES_ESPERADAS = {
    ("__start__", "classify", False),
    # fan-out de classify (todas condicionais em state.route)
    ("classify", "rag", True),
    ("classify", "ticket_ask_tipo", True),
    ("classify", "crud_ask_campo", True),
    ("classify", "check_status", True),
    ("classify", "greeting", True),
    ("classify", "media_download", True),
    ("classify", "sigaa", True),
    ("classify", "human_handoff", True),
    # terminais simples
    ("rag", "__end__", False),
    ("check_status", "__end__", False),
    ("greeting", "__end__", False),
    ("media_download", "__end__", False),
    ("sigaa", "__end__", False),
    ("human_handoff", "__end__", False),
    ("ticket_save", "__end__", False),
    ("crud_save", "__end__", False),
    # funil de ticket
    ("ticket_ask_tipo", "ticket_ask_tipo", True),
    ("ticket_ask_tipo", "ticket_ask_categoria", True),
    ("ticket_ask_tipo", "__end__", True),
    ("ticket_ask_categoria", "ticket_ask_categoria", True),
    ("ticket_ask_categoria", "ticket_ask_queixa", True),
    ("ticket_ask_categoria", "__end__", True),
    ("ticket_ask_queixa", "ticket_ask_queixa", True),
    ("ticket_ask_queixa", "ticket_confirm", True),
    ("ticket_ask_queixa", "__end__", True),
    ("ticket_confirm", "ticket_confirm", True),
    ("ticket_confirm", "ticket_save", True),
    ("ticket_confirm", "__end__", True),
    # funil de CRUD
    ("crud_ask_campo", "crud_ask_campo", True),
    ("crud_ask_campo", "crud_ask_valor", True),
    ("crud_ask_campo", "__end__", True),
    ("crud_ask_valor", "crud_ask_valor", True),
    ("crud_ask_valor", "crud_confirm", True),
    ("crud_ask_valor", "__end__", True),
    ("crud_confirm", "crud_confirm", True),
    ("crud_confirm", "crud_save", True),
    ("crud_confirm", "__end__", True),
}


def _topologia(app=None):
    g = (app or build_graph()).get_graph()
    nodes = {n.id for n in g.nodes.values()}
    edges = {(e.source, e.target, e.conditional) for e in g.edges}
    return nodes, edges


def test_conjunto_de_nos_igual_a_baseline():
    nodes, _ = _topologia()
    assert nodes == NODES_ESPERADOS


def test_conjunto_de_arestas_igual_a_baseline():
    _, edges = _topologia()
    assert edges == EDGES_ESPERADAS


def test_spec_default_equivale_a_baseline():
    """`specs/default.json` compila para EXATAMENTE o mesmo grafo que a
    baseline congelada — é o teste de equivalência da Fase 5 (a topologia
    virou dado sem mudar de forma)."""
    spec = default_spec()
    assert spec.validate_topology() == []
    nodes, edges = _topologia(build_graph_from_spec(spec))
    assert nodes == NODES_ESPERADOS
    assert edges == EDGES_ESPERADAS


def test_build_graph_sem_checkpointer_usa_memorysaver():
    # Não deve levantar mesmo sem Redis Stack (CI usa redis:7-alpine).
    from langgraph.checkpoint.memory import MemorySaver

    app = build_graph()
    assert isinstance(app.checkpointer, MemorySaver)


def test_diagrama_producao_reflete_o_grafo_real():
    from src.application.orchestration.builder import diagrama_producao

    f = diagrama_producao()["fluxos"][0]
    ids = {n["id"] for n in f["nodes"]}
    # funis colapsados
    assert "ticket" in ids and "crud" in ids
    assert "ticket_ask_tipo" not in ids
    # nós reais presentes, com rótulo humano (não o id cru)
    assert "human_handoff" in ids
    hh = next(n for n in f["nodes"] if n["id"] == "human_handoff")
    assert "atendente" in hh["label"].lower() and hh["label"] != "human_handoff"
    # arestas do classify rotuladas
    labels_classify = {e["rotulo"] for e in f["edges"] if e["de"] == "classify"}
    assert all(labels_classify)  # nenhuma vazia
    # sem auto-loop dos funis
    assert not any(e["de"] == e["para"] for e in f["edges"])
