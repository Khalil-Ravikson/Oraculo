"""
src/application/graph/builder.py — Construtor Agentic RAG
"""
from __future__ import annotations
import logging
from langgraph.graph import END, StateGraph
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)
<<<<<<< HEAD
_compiled_graph = None


=======

_compiled_graph = None

>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1
def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        raise RuntimeError("Grafo não inicializado. Chame compilar_grafo() no startup.")
    return _compiled_graph

<<<<<<< HEAD

def get_graph_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def compilar_grafo(oraculo_router) -> object:
=======
def get_graph_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}

def compilar_grafo(oraculo_router):
>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1
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
<<<<<<< HEAD
    from src.application.graph.nodes.core import OraculoCoreNodes

    # Instância com injeção de dependência — resolve o bug da corrotina
    core = OraculoCoreNodes(oraculo_router)
=======
    
    # IMPORTA A NOSSA CLASSE NOVA
    from src.application.graph.nodes.core import OraculoCoreNodes
>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1

    builder = StateGraph(OracleState)
    
    # ── INSTANCIA A CLASSE COM A DEPENDÊNCIA ──
    core_nodes = OraculoCoreNodes(oraculo_router)

    # ── Registro dos nós ──────────────────────────────────────────────────────
    builder.add_node("admin_interceptor_node", node_admin_interceptor)
    builder.add_node("admin_command_node",     node_admin_command)
<<<<<<< HEAD
    builder.add_node("classify_node",          core.node_classify)
    builder.add_node("retrieve_node",          core.node_retrieve)
    builder.add_node("grade_documents_node",   core.node_grade_documents)
    builder.add_node("rewrite_query_node",     core.node_rewrite_query)
    builder.add_node("generate_node",          core.node_generate)
    builder.add_node("crud_node",              node_ask_confirm)
    builder.add_node("exec_tool_node",         node_exec_tool)
    builder.add_node("greeting_node",          node_greeting)
    builder.add_node("respond_node",           node_respond)
=======
    
    # ── ADICIONA OS NÓS DE FORMA NATIVA (SEM LAMBDAS) ──
    builder.add_node("classify_node", core_nodes.node_classify)
    builder.add_node("retrieve_node", core_nodes.node_retrieve)
    builder.add_node("generate_node", core_nodes.node_generate)
    
    builder.add_node("crud_node",      node_ask_confirm)
    builder.add_node("exec_tool_node", node_exec_tool)
    builder.add_node("greeting_node",  node_greeting)
    builder.add_node("respond_node",   node_respond)
>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1

    # ── Ponto de entrada ──────────────────────────────────────────────────────
    builder.set_entry_point("admin_interceptor_node")

<<<<<<< HEAD
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
=======
    # Arestas
    builder.add_conditional_edges("admin_interceptor_node", route_after_interceptor, {
        "admin_command_node": "admin_command_node",
        "respond_node":       "respond_node",
        "classify_node":      "classify_node",
    })
    
    # FIX: Em vez de apontar para "rag_node", apontamos para "retrieve_node"
    builder.add_conditional_edges("classify_node", route_after_classify, {
        "rag_node":           "retrieve_node", # <--- Mudança Aqui!
        "crud_node":          "crud_node",
        "exec_tool_node":     "exec_tool_node",
        "greeting_node":      "greeting_node",
        "admin_command_node": "admin_command_node",
        "respond_node":       "respond_node",
    })

    # O Fluxo Agentic RAG: Retrieve -> Generate -> Respond
    builder.add_edge("retrieve_node", "generate_node")
    builder.add_edge("generate_node", "respond_node")
    
>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1
    builder.add_edge("admin_command_node", "respond_node")
    builder.add_edge("crud_node",          "respond_node")
    builder.add_edge("exec_tool_node",     "respond_node")
    builder.add_edge("greeting_node",      "respond_node")
    builder.add_edge("respond_node",       END)

<<<<<<< HEAD
    checkpointer = _criar_checkpointer()
    graph = builder.compile(
        checkpointer    = checkpointer,
        interrupt_before= ["exec_tool_node"],  # HITL: pausa antes do CRUD
    )
=======
    checkpointer = _criar_redis_saver()
    
    graph = builder.compile(checkpointer=checkpointer, interrupt_before=["exec_tool_node"])
    
    logger.info("✅ Grafo LangGraph Agentic RAG compilado com sucesso.")
>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1
    _compiled_graph = graph
    logger.info("✅ Grafo Agentic RAG (CRAG loop) compilado.")
    return graph


def _criar_checkpointer():
    try:
        from langgraph.checkpoint.redis import RedisSaver
        from src.infrastructure.settings import settings
        return RedisSaver.from_conn_string(settings.REDIS_URL)
    except ImportError:
<<<<<<< HEAD
        logger.warning("⚠️  langgraph-checkpoint-redis não instalado — usando MemorySaver.")
=======
>>>>>>> 1e14e7272f9c6a542742690c81c043e2933aeba1
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()