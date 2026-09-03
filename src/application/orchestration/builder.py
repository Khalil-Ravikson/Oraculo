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
    human_handoff_node,
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
    graph.add_node("human_handoff", _instrumented("human_handoff", human_handoff_node))
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
            "human_handoff": "human_handoff",
        },
    )
    graph.add_edge("rag", END)
    graph.add_edge("check_status", END)
    graph.add_edge("greeting", END)
    graph.add_edge("media_download", END)
    graph.add_edge("sigaa", END)
    graph.add_edge("human_handoff", END)

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
    """Nós + arestas cruas do grafo compilado (sem instanciar checkpointer nem
    chamar nada). Usado por testes e por `diagrama_producao()`."""
    g = build_graph().get_graph()
    return {
        "nodes": sorted(n.id for n in g.nodes.values()),
        "edges": [
            {"source": e.source, "target": e.target, "conditional": e.conditional}
            for e in g.edges
        ],
    }


# Rótulo humano de cada nó do grafo (o painel nunca mostra `rag`/`sigaa`).
_ROTULOS = {
    "__start__": "Mensagem recebida",
    "classify": "Descobrir o assunto (Supervisor)",
    "rag": "Responder com base nos documentos",
    "check_status": "Status de um pedido anterior",
    "greeting": "Saudação",
    "media_download": "Baixar vídeo/mídia",
    "sigaa": "Consultar dados no SIGAA",
    "human_handoff": "Encaminhar a um atendente humano",
    "ticket": "Abrir um chamado (passo a passo)",
    "crud": "Atualizar cadastro (passo a passo)",
    "__end__": "Resposta ao usuário",
}
# Sub-nós dos funis colapsados num nó só no diagrama.
_COLAPSA = {
    "ticket_ask_tipo": "ticket", "ticket_ask_categoria": "ticket",
    "ticket_ask_queixa": "ticket", "ticket_confirm": "ticket", "ticket_save": "ticket",
    "crud_ask_campo": "crud", "crud_ask_valor": "crud",
    "crud_confirm": "crud", "crud_save": "crud",
}


def diagrama_producao() -> dict:
    """Diagrama pronto pra desenhar no Hub (`/hub/graph-studio/reference`).
    Reflete `build_graph()` de verdade — não é dado hardcoded. Os funis de
    ticket/CRUD aparecem colapsados num nó só (o passo-a-passo interno é
    detalhe). Layout em camadas por distância do START."""
    raw = describe()

    def canon(nid: str) -> str:
        return _COLAPSA.get(nid, nid)

    nodes = {canon(n) for n in raw["nodes"]}
    edges = set()
    rotulos_aresta: dict[tuple[str, str], str] = {}
    for e in raw["edges"]:
        s, t = canon(e["source"]), canon(e["target"])
        if s == t:
            continue  # auto-loop dos funis (re-perguntar) — some no diagrama
        edges.add((s, t))
        if e["conditional"] and s == "classify":
            rotulos_aresta[(s, t)] = _ROTULOS.get(t, t)

    # camadas: BFS a partir de __start__
    camada = {"__start__": 0}
    fila = ["__start__"]
    while fila:
        atual = fila.pop(0)
        for (s, t) in edges:
            if s == atual and t not in camada:
                camada[t] = camada[atual] + 1
                fila.append(t)
    for n in nodes:
        camada.setdefault(n, max(camada.values(), default=0))

    por_camada: dict[int, list[str]] = {}
    for n in sorted(nodes):
        por_camada.setdefault(camada[n], []).append(n)

    NODE_W, NODE_H, GAP_X, GAP_Y = 200, 44, 240, 78
    pos_nodes = []
    for c, ids in sorted(por_camada.items()):
        for i, nid in enumerate(ids):
            pos_nodes.append({
                "id": nid, "label": _ROTULOS.get(nid, nid),
                "x": c * GAP_X, "y": i * GAP_Y,
            })

    return {
        "fluxos": [{
            "nome": "Fluxo de produção (grafo real)",
            "descricao": "O que acontece com uma mensagem depois que o assunto é "
                         "descoberto. Gerado do código (orchestration/builder.py), "
                         "não é um desenho manual.",
            "fonte": "src/application/orchestration/builder.py::build_graph",
            "nodes": pos_nodes,
            "edges": [
                {"de": s, "para": t, "rotulo": rotulos_aresta.get((s, t), "")}
                for (s, t) in sorted(edges)
            ],
        }]
    }
