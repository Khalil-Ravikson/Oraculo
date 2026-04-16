"""
application/graph/builder.py — Oráculo UEMA v6.3
================================================
Configuração do LangGraph com persistência em Redis.
"""
from __future__ import annotations

import logging
from typing import Any
from contextlib import AsyncExitStack

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

# ─── Singletons ───────────────────────────────────────────────────────────────
_compiled_graph: Any | None = None
_checkpointer:   AsyncRedisSaver | None = None
_exit_stack:     AsyncExitStack | None = None


def get_compiled_graph() -> Any:
    if _compiled_graph is None:
        raise RuntimeError("Grafo não inicializado. Chame `await init_graph()`.")
    return _compiled_graph


def get_graph_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def init_graph(oraculo_router: Any) -> None:
    """
    Inicializa o grafo com persistência Redis.
    Usa AsyncExitStack para manter o Checkpointer vivo durante a execução da API.
    """
    global _compiled_graph, _checkpointer, _exit_stack

    if _compiled_graph is not None:
        return

    logger.info("⚙️  [GRAPH] Inicializando AsyncRedisSaver + LangGraph...")

    from src.infrastructure.settings import settings

    try:
        # 1. Gerenciamento de Ciclo de Vida via ExitStack
        # Isso garante que a conexão abra agora e feche apenas no shutdown
        _exit_stack = AsyncExitStack()
        
        # 2. Inicialização via String (Evita erro de AttributeError: 'Redis' has no attribute 'startswith')
        _checkpointer = await _exit_stack.enter_async_context(
            AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
        )
        
        # 3. Setup das tabelas internas
        await _checkpointer.setup()
        logger.info("✅ [GRAPH] AsyncRedisSaver (Checkpointer) conectado.")

    except Exception as exc:
        logger.error("🚨 Erro fatal ao configurar Checkpointer: %s", exc)
        if _exit_stack:
            await _exit_stack.aclose()
        raise exc

    # 4. Instanciar nodes
    from src.application.graph.nodes.core import OraculoCoreNodes
    from src.application.graph.nodes.admin import node_admin_interceptor, node_admin_command
    from src.application.graph.nodes.base import node_greeting, node_respond
    from src.application.graph.nodes.tools_exec import node_ask_confirm, node_exec_tool

    core = OraculoCoreNodes(oraculo_router=oraculo_router)

    # 5. Construir o grafo
    sg = StateGraph(OracleState)

    sg.add_node("admin_interceptor_node", node_admin_interceptor)
    sg.add_node("classify_node",          core.node_classify)
    sg.add_node("retrieve_node",          core.node_retrieve)
    sg.add_node("grade_node",             core.node_grade_documents)
    sg.add_node("rewrite_query_node",     core.node_rewrite_query)
    sg.add_node("generate_node",          core.node_generate)
    sg.add_node("greeting_node",          node_greeting)
    sg.add_node("admin_command_node",     node_admin_command)
    sg.add_node("ask_confirm_node",       node_ask_confirm)
    sg.add_node("exec_tool_node",         node_exec_tool)
    sg.add_node("respond_node",           node_respond)

    sg.set_entry_point("admin_interceptor_node")

    from src.application.graph.edges import route_after_interceptor, route_after_classify, route_after_grade

    sg.add_conditional_edges(
        "admin_interceptor_node", route_after_interceptor,
        {"admin_command_node": "admin_command_node", "classify_node": "classify_node", "respond_node": "respond_node"}
    )
    sg.add_conditional_edges(
        "classify_node", route_after_classify,
        {
            "rag_node": "retrieve_node", "crud_node": "ask_confirm_node", 
            "greeting_node": "greeting_node", "admin_command_node": "admin_command_node",
            "respond_node": "respond_node", "exec_tool_node": "exec_tool_node"
        }
    )
    sg.add_conditional_edges(
        "grade_node", route_after_grade,
        {"generate_node": "generate_node", "rewrite_query_node": "rewrite_query_node"}
    )
    
    sg.add_edge("retrieve_node",      "grade_node")
    sg.add_edge("rewrite_query_node", "retrieve_node")
    sg.add_edge("generate_node",      "respond_node")
    sg.add_edge("greeting_node",      "respond_node")
    sg.add_edge("admin_command_node", "respond_node")
    sg.add_edge("ask_confirm_node",   "respond_node")
    sg.add_edge("exec_tool_node",     "respond_node")
    sg.add_edge("respond_node",       END)

    # 6. Compilação Final
    _compiled_graph = sg.compile(
        checkpointer     = _checkpointer,
        interrupt_before = ["exec_tool_node"],
    )

    logger.info("✅ [GRAPH] Grafo compilado e pronto para uso.")


async def aclose_checkpointer() -> None:
    """Encerra as conexões no shutdown."""
    global _checkpointer, _exit_stack
    if _exit_stack is not None:
        try:
            await _exit_stack.aclose()
            logger.info("✅ [GRAPH] Checkpointer desconectado com sucesso.")
        except Exception as exc:
            logger.warning("⚠️  [GRAPH] Erro ao fechar checkpointer: %s", exc)
        finally:
            _checkpointer = None
            _exit_stack = None