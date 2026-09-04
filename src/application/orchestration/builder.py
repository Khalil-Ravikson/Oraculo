"""
src/application/orchestration/builder.py
=======================================
Monta o `StateGraph` do LangGraph a partir de uma `GraphSpec` (ADR 0008
Fase 5) — a topologia é dado (`specs/default.json` / tabela `graph_spec`),
não código. `build_graph()` compila a spec ativa; `build_graph_from_spec()`
compila qualquer spec (usado por testes e pelo preview do Hub).

Sucessor de `langgraph_experiment/graph.py`.
"""
from __future__ import annotations

import functools
import inspect

from langgraph.graph import END, START, StateGraph

from src.application.orchestration import node_manifest, routers
from src.application.orchestration.spec import END_ID, GraphSpec
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


def _mem_checkpointer():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def build_graph_from_spec(spec: GraphSpec, checkpointer=None):
    """Compila uma `GraphSpec` num `CompiledStateGraph`.

    Arestas condicionais que saem do mesmo nó (mesmo `when`) viram um único
    `add_conditional_edges(source, router, {route_value: target})`. Arestas
    simples viram `add_edge`. `interrupt()` continua sendo detalhe interno de
    cada nó (não da spec) — os funis de ticket/CRUD ficam com `locked=True`.

    `checkpointer` é obrigatório para o `interrupt()` funcionar entre turnos.
    Sem ele (None) usa MemorySaver — processo único, testes / REPL sem Redis
    Stack; produção passa o AsyncRedisSaver (ver entrypoint.py).
    """
    erros = spec.validate_topology()
    if erros:
        raise ValueError("GraphSpec inválida:\n  - " + "\n  - ".join(erros))

    if checkpointer is None:
        checkpointer = _mem_checkpointer()

    graph = StateGraph(OraculoState)
    for n in spec.nodes:
        tipo = node_manifest.get_tipo(n.type)
        graph.add_node(n.id, _instrumented(n.id, tipo.fn))

    graph.add_edge(START, spec.entrypoint)

    for source, grupo in spec.edges_por_source().items():
        condicionais = [e for e in grupo if e.when is not None]
        if condicionais:
            router_fn = routers.get_router(condicionais[0].when)
            mapping = {
                e.route_value: (END if e.target == END_ID else e.target)
                for e in condicionais
            }
            graph.add_conditional_edges(source, router_fn, mapping)
        else:
            for e in grupo:
                graph.add_edge(source, END if e.target == END_ID else e.target)

    return graph.compile(checkpointer=checkpointer)


_ULTIMO_ROUTE_VALUES: frozenset[str] = frozenset()


def route_values_ativos() -> frozenset[str]:
    """`route_value`s que o fan-out de `classify` (router `by_state_route`)
    sabe resolver na ÚLTIMA spec compilada por `build_graph()` — usado por
    `nodes.classify_node` como rede de segurança (rota classificada cujo
    `entrypoint_node` não é mais um destino válido do fan-out cai em `rag`).

    NÃO é o conjunto de ids de nó do grafo compilado — `route_value` (ex.:
    "ticket", "crud") e id de nó (ex.: "ticket_ask_tipo", "crud_ask_campo")
    vivem em namespaces DIFERENTES pros funis; comparar `entrypoint_node`
    contra ids de nó rejeitaria "ticket"/"crud" sempre (bug achado ao migrar
    o circuit-breaker pra dentro do grafo, Fase B — mascarado até então
    porque nenhum teste populava essa checagem pra rota de funil).

    Vazio até o primeiro `build_graph()` da vida do processo — nesse caso a
    checagem do lado de quem chama fica leniente (nunca bloqueia por falta
    de dado). Deliberadamente só `build_graph()` (a spec ATIVA) atualiza isto
    — specs arbitrárias montadas via `build_graph_from_spec()` em
    teste/preview não devem vazar pra essa checagem em produção."""
    return _ULTIMO_ROUTE_VALUES


def build_graph(checkpointer=None):
    """Compila a `GraphSpec` ATIVA (Redis → Postgres → `specs/default.json`).
    Ponto de entrada usado por `entrypoint.py::_get_graph`."""
    global _ULTIMO_ROUTE_VALUES
    from src.application.orchestration.loader import carregar_spec_ativa
    spec = carregar_spec_ativa()
    app = build_graph_from_spec(spec, checkpointer)
    _ULTIMO_ROUTE_VALUES = frozenset(
        e.route_value for e in spec.edges
        if e.when == "by_state_route" and e.route_value not in (None, END_ID)
    )
    return app


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
    "classify": "Descobrir o assunto e checar se a função está ativa",
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
    """Diagrama pronto pra desenhar no Hub. Reflete `build_graph()` (a
    `GraphSpec` ativa) de verdade — não é dado hardcoded. Os funis de
    ticket/CRUD aparecem colapsados num nó só (o passo-a-passo interno é
    detalhe). Layout em camadas por distância do START."""
    raw = describe()

    tipo_por_id: dict[str, str] = {}
    try:
        from src.application.orchestration.loader import carregar_spec_ativa
        tipo_por_id = {n.id: n.type for n in carregar_spec_ativa().nodes}
    except Exception:  # noqa: BLE001
        pass

    def _rotulo(nid: str) -> str:
        if nid in _ROTULOS:
            return _ROTULOS[nid]
        tipo = tipo_por_id.get(nid)
        if tipo and tipo in node_manifest.tipos_registrados():
            return f"{node_manifest.get_tipo(tipo).display_name}: {nid}"
        return nid

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
            rotulos_aresta[(s, t)] = _rotulo(t)

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
                "id": nid, "label": _rotulo(nid),
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
