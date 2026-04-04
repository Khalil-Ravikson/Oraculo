# src/application/graph/edges.py
"""
Arestas condicionais do LangGraph — lógica de roteamento pura.

PRINCÍPIO: separar roteamento de execução.
  - nodes.py → O QUE fazer
  - edges.py → PARA ONDE ir depois

GRAFO COMPLETO:
  START
    → admin_interceptor
        → (is_admin + command) → admin_command → respond → END
        → (not admin / normal msg) → classify
    → classify
        → "rag"          → rag_node → respond → END
        → "crud"         → ask_confirm_node → [INTERRUPT]
        → "greeting"     → greeting_node → respond → END
        → "admin"        → admin_command → respond → END
        → "respond_only" → respond → END
    → ask_confirm_node   → [INTERRUPT: interrupt_before=exec_tool_node]
    → exec_tool_node → respond → END
"""
from __future__ import annotations

import logging
from typing import Literal

from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

# Tipo das rotas possíveis
Route = Literal[
    "rag_node",
    "crud_node",
    "exec_tool_node",
    "greeting_node",
    "admin_command_node",
    "respond_node",
]


def route_after_interceptor(state: OracleState) -> Route:
    """
    Aresta após o AdminInterceptorNode.
    Se é admin com comando → admin_command_node.
    Caso contrário → classify_node (fluxo normal).
    """
    if state.get("route") == "admin":
        return "admin_command_node"
    if state.get("route") == "respond_only":
        return "respond_node"
    return "classify_node"


def route_after_classify(state: OracleState) -> Route:
    """
    Aresta principal — decide o caminho após a classificação.

    CASOS ESPECIAIS:
      - confirmation_result = "confirmed" → vai direto para exec_tool
        (o usuário já confirmou em um turno anterior)
      - route = "respond_only" → resposta já montada, só entrega
    """
    # Retomada HITL após confirmação
    if state.get("confirmation_result") == "confirmed":
        logger.debug("✅ HITL confirmado → exec_tool_node")
        return "exec_tool_node"

    # Resposta já pronta (HITL cancelado, modo manutenção, etc.)
    if state.get("route") == "respond_only":
        return "respond_node"

    route = state.get("route", "rag")
    mapping: dict[str, Route] = {
        "rag":      "rag_node",
        "geral":    "rag_node",
        "crud":     "crud_node",       # crud → ask_confirm (interrupt antes de exec)
        "greeting": "greeting_node",
        "admin":    "admin_command_node",
    }
    return mapping.get(route, "rag_node")


def route_after_crud(state: OracleState) -> Route:
    """
    Após ask_confirm_node, sempre vai para respond.
    O interrupt acontece ANTES de exec_tool_node — não aqui.
    """
    return "respond_node"