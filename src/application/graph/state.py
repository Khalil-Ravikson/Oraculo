# src/application/graph/state.py
from __future__ import annotations
from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class OracleState(TypedDict):
    # ── Identidade (imutável por conversa) ───────────────────────────────────
    user_id:     str
    chat_id:     str
    user_name:   str
    user_role:   Literal["guest", "student", "admin", "professor", "servidor"]
    user_status: str
    is_admin:    bool
    curso:       Optional[str]
    periodo:     Optional[str]
    matricula:   Optional[str]
    centro:      Optional[str]

    # ── Mensagem atual ────────────────────────────────────────────────────────
    current_input: str
    has_media:     bool

    # ── Histórico oficial (reducer do LangGraph — NUNCA sobrescrever) ─────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── Roteamento ────────────────────────────────────────────────────────────
    route:         str
    tool_name:     Optional[str]
    tool_args:     Optional[dict]

    # ── Agentic RAG ───────────────────────────────────────────────────────────
    rag_context:   Optional[str]   # contexto recuperado do Redis
    crag_score:    float           # qualidade do retrieval (0.0–1.0)
    relevance:     str             # "yes" | "no" (resultado do grade_documents)
    loop_count:    int             # quantas vezes reescrevemos a query (máx 2)

    # ── HITL ──────────────────────────────────────────────────────────────────
    pending_confirmation: Optional[str]
    confirmation_result:  Optional[str]  # "confirmed" | "cancelled" | "pending"

    # ── Admin ─────────────────────────────────────────────────────────────────
    admin_token_verified: bool
    admin_command:        Optional[str]

    # ── Output final ──────────────────────────────────────────────────────────
    final_response: Optional[str]

    @classmethod
    def from_identity(cls, identity: dict, messages: list | None = None) -> "OracleState":
        return {
            "user_id":     identity.get("user_id", ""),
            "chat_id":     identity.get("chat_id", ""),
            "user_name":   identity.get("nome", "Utilizador"),
            "user_role":   identity.get("role", "guest"),
            "user_status": identity.get("status", "pendente"),
            "is_admin":    identity.get("is_admin", False),
            "curso":       identity.get("curso"),
            "periodo":     identity.get("periodo"),
            "matricula":   identity.get("matricula"),
            "centro":      identity.get("centro"),
            "current_input": identity.get("body", ""),
            "has_media":     identity.get("has_media", False),
            "messages":      messages or [],
            "route":         "rag",
            "tool_name":     None,
            "tool_args":     None,
            "rag_context":   None,
            "crag_score":    0.0,
            "relevance":     "yes",
            "loop_count":    0,
            "pending_confirmation": None,
            "confirmation_result":  None,
            "admin_token_verified": False,
            "admin_command":        None,
            "final_response": None,
        }