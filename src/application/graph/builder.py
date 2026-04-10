# src/application/graph/builder.py
from __future__ import annotations
import logging
from langgraph.graph import END, StateGraph
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)
_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        raise RuntimeError("Grafo não inicializado. Chame compilar_grafo() no startup.")
    return _compiled_graph


def get_graph_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def compilar_grafo(oraculo_router) -> object:
    global _compiled_graph
    from typing import Literal

    from src.application.graph.edges import (
        route_after_classify,
        route_after_interceptor,
        route_after_grade,        # ← NOVA aresta CRAG
    )
    from src.application.graph.nodes.admin import node_admin_interceptor, node_admin_command
    from src.application.graph.nodes.tools_exec import node_ask_confirm, node_exec_tool
    from src.application.graph.nodes.base import node_greeting, node_respond
    from src.application.graph.nodes.core import OraculoCoreNodes

    # Instância com injeção de dependência — resolve o bug da corrotina
    core = OraculoCoreNodes(oraculo_router)

    builder = StateGraph(OracleState)

    # ── Registro dos nós ──────────────────────────────────────────────────────
    builder.add_node("admin_interceptor_node", node_admin_interceptor)
    builder.add_node("admin_command_node",     node_admin_command)
    builder.add_node("classify_node",          core.node_classify)
    builder.add_node("retrieve_node",          core.node_retrieve)
    builder.add_node("grade_documents_node",   core.node_grade_documents)
    builder.add_node("rewrite_query_node",     core.node_rewrite_query)
    builder.add_node("generate_node",          core.node_generate)
    builder.add_node("crud_node",              node_ask_confirm)
    builder.add_node("exec_tool_node",         node_exec_tool)
    builder.add_node("greeting_node",          node_greeting)
    builder.add_node("respond_node",           node_respond)

    # ── Ponto de entrada ──────────────────────────────────────────────────────
    builder.set_entry_point("admin_interceptor_node")

    # ── Arestas ───────────────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "admin_interceptor_node",
        route_after_interceptor,
        {
            "admin_command_node": "admin_command_node",
            "respond_node":       "respond_node",
            "classify_node":      "classify_node",
        },
    )

    builder.add_conditional_edges(
        "classify_node",
        route_after_classify,
        {
            "rag_node":           "retrieve_node",   # entry do pipeline RAG
            "crud_node":          "crud_node",
            "exec_tool_node":     "exec_tool_node",
            "greeting_node":      "greeting_node",
            "admin_command_node": "admin_command_node",
            "respond_node":       "respond_node",
        },
    )

    # ── O Loop CRAG ───────────────────────────────────────────────────────────
    builder.add_edge("retrieve_node",      "grade_documents_node")
    builder.add_conditional_edges(
        "grade_documents_node",
        route_after_grade,
        {
            "generate_node":      "generate_node",
            "rewrite_query_node": "rewrite_query_node",
        },
    )
    builder.add_edge("rewrite_query_node", "retrieve_node")   # FECHA O LOOP
    builder.add_edge("generate_node",      "respond_node")

    # ── Demais arestas ────────────────────────────────────────────────────────
    builder.add_edge("admin_command_node", "respond_node")
    builder.add_edge("crud_node",          "respond_node")
    builder.add_edge("exec_tool_node",     "respond_node")
    builder.add_edge("greeting_node",      "respond_node")
    builder.add_edge("respond_node",       END)

    checkpointer = _criar_checkpointer()
    graph = builder.compile(
        checkpointer    = checkpointer,
        interrupt_before= ["exec_tool_node"],  # HITL: pausa antes do CRUD
    )
    _compiled_graph = graph
    logger.info("✅ Grafo Agentic RAG (CRAG loop) compilado.")
    return graph


def _criar_checkpointer():
    try:
        from langgraph.checkpoint.redis import RedisSaver
        from src.infrastructure.settings import settings
        return RedisSaver.from_conn_string(settings.REDIS_URL)
    except ImportError:
        logger.warning("⚠️  langgraph-checkpoint-redis não instalado — usando MemorySaver.")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()