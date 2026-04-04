# src/application/graph/builder.py
"""
Compilação do LangGraph com RedisSaver para persistência real.

MUDANÇAS vs versão anterior:
  - RedisSaver substitui MemorySaver → estado sobrevive a restarts
  - AdminInterceptorNode adicionado no topo do grafo
  - edges.py separado para clareza
  - interrupt_before=["exec_tool_node"] mantido

POR QUE RedisSaver É CRÍTICO:
  - MemorySaver: estado vive na RAM → perdido se o worker Celery reiniciar
  - RedisSaver:  estado vive no Redis → HITL funciona mesmo após deploy

THREAD DO GRAFO:
  thread_id = phone do usuário → cada aluno tem sua própria "conversa" persistente
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.graph import END, StateGraph

from src.application.graph.edges import (
    route_after_classify,
    route_after_crud,
    route_after_interceptor,
)
from src.application.graph.nodes import (
    node_admin_command,
    node_admin_interceptor,
    node_ask_confirm,
    node_classify,
    node_exec_tool,
    node_greeting,
    node_rag,
    node_respond,
)
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)


def _criar_redis_saver():
    """
    Cria o RedisSaver para persistência do estado do LangGraph.

    COMPATIBILIDADE: LangGraph >= 0.0.39 suporta RedisSaver.
    Fallback para MemorySaver se Redis estiver offline.
    """
    try:
        # Tentativa com a API do langgraph>=0.0.39
        from langgraph.checkpoint.redis import RedisSaver
        from src.infrastructure.settings import settings
        saver = RedisSaver.from_conn_string(settings.REDIS_URL)
        logger.info("✅ RedisSaver configurado — estado LangGraph persistente.")
        return saver
    except ImportError:
        logger.warning(
            "⚠️  langgraph[redis] não instalado. "
            "Usando MemorySaver (estado NÃO persiste entre restarts).\n"
            "Para persistência real: pip install langgraph[redis]"
        )
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    except Exception as e:
        logger.error("❌ RedisSaver falhou (%s). Usando MemorySaver.", e)
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


@lru_cache(maxsize=1)
def get_compiled_graph():
    """
    Singleton: o grafo é compilado UMA VEZ por processo Celery.

    ESTRUTURA DO GRAFO:
    ────────────────────
    START
      └── admin_interceptor
            ├── (admin + cmd)   → admin_command → respond → END
            ├── (respond_only)  → respond → END
            └── (normal)        → classify
                  ├── (rag)       → rag      → respond → END
                  ├── (crud)      → ask_confirm → respond → END
                  │                 [INTERRUPT antes de exec_tool]
                  │               → exec_tool → respond → END
                  ├── (greeting)  → greeting → respond → END
                  ├── (admin)     → admin_command → respond → END
                  └── (respond)   → respond → END
    """
    logger.info("🔧 Compilando grafo LangGraph v3...")
    builder = StateGraph(OracleState)

    # ── Registra nós ──────────────────────────────────────────────────────────
    builder.add_node("admin_interceptor_node", node_admin_interceptor)
    builder.add_node("admin_command_node",     node_admin_command)
    builder.add_node("classify_node",          node_classify)
    builder.add_node("rag_node",               node_rag)
    builder.add_node("crud_node",              node_ask_confirm)
    builder.add_node("exec_tool_node",         node_exec_tool)
    builder.add_node("greeting_node",          node_greeting)
    builder.add_node("respond_node",           node_respond)

    # ── Entrypoint ────────────────────────────────────────────────────────────
    builder.set_entry_point("admin_interceptor_node")

    # ── Arestas condicionais ──────────────────────────────────────────────────
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
            "rag_node":           "rag_node",
            "crud_node":          "crud_node",
            "exec_tool_node":     "exec_tool_node",
            "greeting_node":      "greeting_node",
            "admin_command_node": "admin_command_node",
            "respond_node":       "respond_node",
        },
    )

    # ── Arestas fixas ─────────────────────────────────────────────────────────
    builder.add_edge("admin_command_node", "respond_node")
    builder.add_edge("rag_node",           "respond_node")
    builder.add_edge("crud_node",          "respond_node")  # pede confirmação → respond
    builder.add_edge("exec_tool_node",     "respond_node")
    builder.add_edge("greeting_node",      "respond_node")
    builder.add_edge("respond_node",       END)

    # ── Compila com RedisSaver e interrupt_before ─────────────────────────────
    checkpointer = _criar_redis_saver()

    graph = builder.compile(
        checkpointer=checkpointer,
        # O grafo PAUSA aqui e aguarda a próxima mensagem do usuário
        # antes de executar a tool CRUD
        interrupt_before=["exec_tool_node"],
    )

    logger.info("✅ Grafo LangGraph compilado com %d nós.", len(builder.nodes))
    return graph


def get_graph_config(thread_id: str) -> dict:
    """
    Configuração do grafo por thread (uma por usuário).
    O thread_id garante que cada conversa é isolada no RedisSaver.
    """
    return {"configurable": {"thread_id": thread_id}}