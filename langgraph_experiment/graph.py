from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from langgraph_experiment.nodes import classify_node, rag_node, ticket_node
from langgraph_experiment.state import OraculoState


def build_graph(checkpointer=None):
    """
    Monta o StateGraph mínimo: classify -> (rag | ticket).
    `checkpointer` é obrigatório para o `interrupt()` funcionar entre turnos
    (persiste onde a execução parou). Por padrão usa MemorySaver (processo
    único, só para este teste manual); ver run_test.py para a opção Redis
    (equivalente ao redis_state.py usado hoje em produção).
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    graph = StateGraph(OraculoState)
    graph.add_node("classify", classify_node)
    graph.add_node("rag", rag_node)
    graph.add_node("ticket", ticket_node)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: state["route"],
        {"rag": "rag", "ticket": "ticket"},
    )
    graph.add_edge("rag", END)
    graph.add_edge("ticket", END)

    return graph.compile(checkpointer=checkpointer)
