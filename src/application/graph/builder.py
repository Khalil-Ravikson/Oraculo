# src/application/graph/builder.py
from __future__ import annotations
from functools import lru_cache

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import OracleState
from .nodes import (
    node_classify,
    node_rag,
    node_ask_confirm,
    node_exec_tool,
    node_greeting,
    node_respond,
)


def _route_after_classify(state: OracleState) -> str:
    """Edge condicional após classificação."""
    # Retomada HITL com confirmação positiva
    if state.get("confirmation_result") == "confirmed":
        return "exec_tool"

    # Cancelamento ou resposta ambígua já tratada — vai direto ao respond
    route = state.get("route", "rag")
    if route == "respond_only":
        return "respond"

    return route  # "rag" | "crud" | "greeting"


@lru_cache(maxsize=1)
def get_compiled_graph():
    """
    Singleton: o grafo é compilado uma única vez por processo.

    NOTA sobre checkpointer:
    MemorySaver é adequado para desenvolvimento e para processos únicos.
    Em produção com múltiplos workers Celery, substituir por
    RedisSaver quando disponível na versão instalada do LangGraph.
    """
    builder = StateGraph(OracleState)

    # ── Registra nós ──────────────────────────────────────────────
    builder.add_node("classify",    node_classify)
    builder.add_node("rag",         node_rag)
    builder.add_node("crud",        node_ask_confirm)   # crud → pede confirmação
    builder.add_node("exec_tool",   node_exec_tool)     # executa após confirmação
    builder.add_node("greeting",    node_greeting)
    builder.add_node("respond",     node_respond)

    # ── Define fluxo ──────────────────────────────────────────────
    builder.set_entry_point("classify")

    builder.add_conditional_edges(
        "classify",
        _route_after_classify,
        {
            "rag":       "rag",
            "crud":      "crud",
            "exec_tool": "exec_tool",
            "greeting":  "greeting",
            "respond":   "respond",
        },
    )

    builder.add_edge("rag",      "respond")
    builder.add_edge("crud",     "respond")   # responde com a pergunta de confirmação
    builder.add_edge("exec_tool","respond")
    builder.add_edge("greeting", "respond")
    builder.add_edge("respond",  END)

    # ── Compila com interrupt_before para HITL ────────────────────
    checkpointer = MemorySaver()

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["exec_tool"],
    )