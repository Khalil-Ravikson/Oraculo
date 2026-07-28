from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from langgraph_experiment.nodes import (
    classify_node,
    rag_node,
    ticket_ask_categoria,
    ticket_ask_queixa,
    ticket_ask_tipo,
    ticket_confirm,
    ticket_save,
    _categoria_valida,
    _confirm_route,
    _queixa_valida,
    _tipo_valido,
)
from langgraph_experiment.state import OraculoState


def build_graph(checkpointer=None):
    """
    Monta o StateGraph: classify -> (rag | funil de ticket).

    Funil de ticket é uma sequência de nodes (1 interrupt() cada:
    ticket_ask_tipo -> ticket_ask_categoria -> ticket_ask_queixa ->
    ticket_confirm -> ticket_save), não um único node com vários interrupt()
    empilhados — ver docstring de nodes.py pro motivo (bug conhecido do
    checkpointer Redis com múltiplos interrupts pendentes no mesmo node).

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
    graph.add_node("ticket_ask_tipo", ticket_ask_tipo)
    graph.add_node("ticket_ask_categoria", ticket_ask_categoria)
    graph.add_node("ticket_ask_queixa", ticket_ask_queixa)
    graph.add_node("ticket_confirm", ticket_confirm)
    graph.add_node("ticket_save", ticket_save)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        lambda state: state.route,
        {"rag": "rag", "ticket": "ticket_ask_tipo"},
    )
    graph.add_edge("rag", END)

    graph.add_conditional_edges(
        "ticket_ask_tipo", _tipo_valido,
        {"ticket_ask_tipo": "ticket_ask_tipo", "ticket_ask_categoria": "ticket_ask_categoria"},
    )
    graph.add_conditional_edges(
        "ticket_ask_categoria", _categoria_valida,
        {"ticket_ask_categoria": "ticket_ask_categoria", "ticket_ask_queixa": "ticket_ask_queixa"},
    )
    graph.add_conditional_edges(
        "ticket_ask_queixa", _queixa_valida,
        {"ticket_ask_queixa": "ticket_ask_queixa", "ticket_confirm": "ticket_confirm"},
    )
    graph.add_conditional_edges(
        "ticket_confirm", _confirm_route,
        {"ticket_confirm": "ticket_confirm", "ticket_save": "ticket_save", "__end__": END},
    )
    graph.add_edge("ticket_save", END)

    return graph.compile(checkpointer=checkpointer)
