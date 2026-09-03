"""
src/application/orchestration/builder.py
=======================================
Monta o `StateGraph` do LangGraph a partir dos nós de orquestração.

Sucessor de `langgraph_experiment/graph.py`. Nesta fase (0) a topologia
ainda é declarada aqui em código; a Fase 5 (ADR 0008) move a topologia do
fan-out simples pra `GraphSpec` (dado), mantendo os funis de ticket/CRUD
como subgrafos de código.
"""
from __future__ import annotations

import functools
import inspect

from langgraph.graph import END, START, StateGraph

from src.application.orchestration.nodes import (
    check_status_node,
    classify_node,
    crud_ask_campo,
    crud_ask_valor,
    crud_confirm,
    crud_save,
    greeting_node,
    media_download_node,
    rag_node,
    sigaa_node,
    ticket_ask_categoria,
    ticket_ask_queixa,
    ticket_ask_tipo,
    ticket_confirm,
    ticket_save,
    _campo_crud_valido,
    _categoria_valida,
    _confirm_route,
    _crud_confirm_route,
    _queixa_valida,
    _tipo_valido,
    _valor_crud_valido,
)
from src.application.orchestration.state import OraculoState


def _get_or_create_metric(metric_cls, name, documentation, labelnames=()):
    """Mesmo padrão de `router/supervisor.py::_get_or_create_metric` — evita
    'Duplicated timeseries in CollectorRegistry' em hot-reload/import repetido."""
    from prometheus_client import REGISTRY
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return metric_cls(name, documentation, labelnames=labelnames)


def _instrumented(node_name: str, fn):
    """Envolve um node do grafo pra contar execuções por node
    (`oraculo_langgraph_node_total{node}`) — único jeito de saber, no
    Grafana, qual node do grafo de orquestração rodou (a telemetria de
    custo/cache já flui sozinha, porque os nodes chamam RAGSearchService/
    SynthesisService/SemanticCache reais de produção — ver nodes.py)."""
    from prometheus_client import Counter

    counter = _get_or_create_metric(
        Counter, "oraculo_langgraph_node_total",
        "Execuções por node do grafo de orquestração", ["node"],
    )

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _async_wrapper(*args, **kwargs):
            counter.labels(node=node_name).inc()
            return await fn(*args, **kwargs)
        return _async_wrapper

    @functools.wraps(fn)
    def _sync_wrapper(*args, **kwargs):
        counter.labels(node=node_name).inc()
        return fn(*args, **kwargs)
    return _sync_wrapper


def build_graph(checkpointer=None):
    """
    Monta o StateGraph: classify -> (rag | funil de ticket | funil de CRUD).

    Funil de ticket e funil de CRUD são sequências de nodes (1 interrupt()
    cada), não um único node com vários interrupt() empilhados — ver
    docstring de nodes.py pro motivo (bug conhecido do checkpointer Redis
    com múltiplos interrupts pendentes no mesmo node).

    `checkpointer` é obrigatório para o `interrupt()` funcionar entre turnos
    (persiste onde a execução parou). Por padrão usa MemorySaver (processo
    único, só para teste manual / unit tests sem Redis Stack); produção
    passa o AsyncRedisSaver (ver entrypoint.py).
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    graph = StateGraph(OraculoState)
    graph.add_node("classify", _instrumented("classify", classify_node))
    graph.add_node("rag", _instrumented("rag", rag_node))
    graph.add_node("check_status", _instrumented("check_status", check_status_node))
    graph.add_node("greeting", _instrumented("greeting", greeting_node))
    graph.add_node("media_download", _instrumented("media_download", media_download_node))
    graph.add_node("sigaa", _instrumented("sigaa", sigaa_node))
    graph.add_node("ticket_ask_tipo", _instrumented("ticket_ask_tipo", ticket_ask_tipo))
    graph.add_node("ticket_ask_categoria", _instrumented("ticket_ask_categoria", ticket_ask_categoria))
    graph.add_node("ticket_ask_queixa", _instrumented("ticket_ask_queixa", ticket_ask_queixa))
    graph.add_node("ticket_confirm", _instrumented("ticket_confirm", ticket_confirm))
    graph.add_node("ticket_save", _instrumented("ticket_save", ticket_save))
    graph.add_node("crud_ask_campo", _instrumented("crud_ask_campo", crud_ask_campo))
    graph.add_node("crud_ask_valor", _instrumented("crud_ask_valor", crud_ask_valor))
    graph.add_node("crud_confirm", _instrumented("crud_confirm", crud_confirm))
    graph.add_node("crud_save", _instrumented("crud_save", crud_save))

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: state.route,
        {
            "rag": "rag", "ticket": "ticket_ask_tipo", "crud": "crud_ask_campo",
            # Fase 2d (Decisão 01) — só alcançáveis com
            # settings.FEATURE_LANGGRAPH_NATIVE_ROUTES ligada (ver
            # dispatcher_langgraph.py::_ROTAS_LANGGRAPH_NATIVAS_CONDICIONAIS).
            "check_status": "check_status", "greeting": "greeting",
            "media_download": "media_download", "sigaa": "sigaa",
        },
    )
    graph.add_edge("rag", END)
    graph.add_edge("check_status", END)
    graph.add_edge("greeting", END)
    graph.add_edge("media_download", END)
    graph.add_edge("sigaa", END)

    graph.add_conditional_edges(
        "ticket_ask_tipo", _tipo_valido,
        {"ticket_ask_tipo": "ticket_ask_tipo", "ticket_ask_categoria": "ticket_ask_categoria", "__end__": END},
    )
    graph.add_conditional_edges(
        "ticket_ask_categoria", _categoria_valida,
        {"ticket_ask_categoria": "ticket_ask_categoria", "ticket_ask_queixa": "ticket_ask_queixa", "__end__": END},
    )
    graph.add_conditional_edges(
        "ticket_ask_queixa", _queixa_valida,
        {"ticket_ask_queixa": "ticket_ask_queixa", "ticket_confirm": "ticket_confirm", "__end__": END},
    )
    graph.add_conditional_edges(
        "ticket_confirm", _confirm_route,
        {"ticket_confirm": "ticket_confirm", "ticket_save": "ticket_save", "__end__": END},
    )
    graph.add_edge("ticket_save", END)

    graph.add_conditional_edges(
        "crud_ask_campo", _campo_crud_valido,
        {"crud_ask_campo": "crud_ask_campo", "crud_ask_valor": "crud_ask_valor", "__end__": END},
    )
    graph.add_conditional_edges(
        "crud_ask_valor", _valor_crud_valido,
        {"crud_ask_valor": "crud_ask_valor", "crud_confirm": "crud_confirm", "__end__": END},
    )
    graph.add_conditional_edges(
        "crud_confirm", _crud_confirm_route,
        {"crud_confirm": "crud_confirm", "crud_save": "crud_save", "__end__": END},
    )
    graph.add_edge("crud_save", END)

    return graph.compile(checkpointer=checkpointer)


def describe() -> dict:
    """Nós + arestas do grafo de produção real, pro visualizador do Hub
    (`/hub/graph/producao`, ADR 0008 Fase 4) — substitui `reference_flows.py`
    hardcoded. Somente leitura; não instancia checkpointer nem chama nada."""
    g = build_graph().get_graph()
    return {
        "nodes": sorted(n.id for n in g.nodes.values()),
        "edges": [
            {"source": e.source, "target": e.target, "conditional": e.conditional}
            for e in g.edges
        ],
    }
