"""
tests/unit/orchestration/test_builder.py
========================================
Trava a topologia do grafo de orquestração (`src/application/orchestration/
builder.py::build_graph`).

Fase 0 (ADR 0008): este teste congela o conjunto de nós e arestas herdado de
`langgraph_experiment/graph.py` no momento da migração — é a rede de segurança
que garante que mover os módulos não mudou o grafo. Quando as Fases 1-5
adicionarem nós de front (intake/gate/policy/human_handoff) a atualização
desta baseline deve ser DELIBERADA, num commit que também explica a mudança.
"""
from __future__ import annotations

from src.application.orchestration.builder import build_graph

# Baseline congelada da Fase 0 — igual a langgraph_experiment/graph.py.
NODES_ESPERADOS = {
    "__start__", "__end__",
    "classify", "rag", "check_status", "greeting", "media_download", "sigaa",
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
    # terminais simples
    ("rag", "__end__", False),
    ("check_status", "__end__", False),
    ("greeting", "__end__", False),
    ("media_download", "__end__", False),
    ("sigaa", "__end__", False),
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


def _topologia():
    g = build_graph().get_graph()
    nodes = {n.id for n in g.nodes.values()}
    edges = {(e.source, e.target, e.conditional) for e in g.edges}
    return nodes, edges


def test_conjunto_de_nos_igual_a_baseline():
    nodes, _ = _topologia()
    assert nodes == NODES_ESPERADOS


def test_conjunto_de_arestas_igual_a_baseline():
    _, edges = _topologia()
    assert edges == EDGES_ESPERADAS


def test_build_graph_sem_checkpointer_usa_memorysaver():
    # Não deve levantar mesmo sem Redis Stack (CI usa redis:7-alpine).
    from langgraph.checkpoint.memory import MemorySaver

    app = build_graph()
    assert isinstance(app.checkpointer, MemorySaver)
