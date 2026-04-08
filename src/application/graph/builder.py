# src/application/graph/builder.py
from __future__ import annotations
import logging
from functools import lru_cache
from langgraph.graph import END, StateGraph
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_compiled_graph():
    """
    O grafo compilado é IMUTÁVEL — partilhá-lo entre threads é safe.
    O estado vive no RedisSaver por thread_id (phone), não na instância.
    """
    from src.application.graph.edges import route_after_classify, route_after_interceptor
    
    # IMPORTAÇÕES CORRIGIDAS: Cada nó vem do seu ficheiro correto!
    from src.application.graph.nodes.admin import node_admin_interceptor, node_admin_command
    from src.application.graph.nodes.core import node_classify, node_rag
    from src.application.graph.nodes.tools_exec import node_ask_confirm, node_exec_tool
    from src.application.graph.nodes.base import node_greeting, node_respond

    builder = StateGraph(OracleState)

    builder.add_node("admin_interceptor_node", node_admin_interceptor)
    builder.add_node("admin_command_node",     node_admin_command)
    builder.add_node("classify_node",          node_classify)
    builder.add_node("rag_node",               node_rag)
    builder.add_node("crud_node",              node_ask_confirm)
    builder.add_node("exec_tool_node",         node_exec_tool)
    builder.add_node("greeting_node",          node_greeting)
    builder.add_node("respond_node",           node_respond)

    builder.set_entry_point("admin_interceptor_node")

    builder.add_conditional_edges("admin_interceptor_node", route_after_interceptor, {
        "admin_command_node": "admin_command_node",
        "respond_node":       "respond_node",
        "classify_node":      "classify_node",
    })
    
    builder.add_conditional_edges("classify_node", route_after_classify, {
        "rag_node":           "rag_node",
        "crud_node":          "crud_node",
        "exec_tool_node":     "exec_tool_node",
        "greeting_node":      "greeting_node",
        "admin_command_node": "admin_command_node",
        "respond_node":       "respond_node",
    })

    builder.add_edge("admin_command_node", "respond_node")
    builder.add_edge("rag_node",           "respond_node")
    builder.add_edge("crud_node",          "respond_node")
    builder.add_edge("exec_tool_node",     "respond_node")
    builder.add_edge("greeting_node",      "respond_node")
    builder.add_edge("respond_node",       END)

    checkpointer = _criar_redis_saver()
    
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["exec_tool_node"],
    )
    
    logger.info("✅ Grafo LangGraph compilado e pronto.")
    return graph


def _criar_redis_saver():
    try:
        from langgraph.checkpoint.redis import RedisSaver
        from src.infrastructure.settings import settings
        return RedisSaver.from_conn_string(settings.REDIS_URL)
    except ImportError:
        logger.warning("⚠️ langgraph[redis] não instalado. Usando MemorySaver para testes.")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


def get_graph_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}