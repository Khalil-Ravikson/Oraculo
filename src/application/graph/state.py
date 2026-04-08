# src/application/graph/state.py
from __future__ import annotations
from typing import Annotated, Any, Literal, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class OracleState(TypedDict):
    # Identidade (imutável durante a conversa)
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

    # Mensagem actual
    current_input: str
    has_media:     bool

    # Histórico (gerido pelo LangGraph com reducer add_messages)
    # NUNCA sobrescrever directamente — usar add_messages como reducer
    messages: Annotated[list[BaseMessage], add_messages]

    # Roteamento
    route:     str
    tool_name: Optional[str]
    tool_args: Optional[dict]

    # HITL
    pending_confirmation: Optional[str]
    confirmation_result:  Optional[str]  # "confirmed" | "cancelled" | "pending"

    # Admin
    admin_token_verified: bool
    admin_command:        Optional[str]

    # Output
    final_response: Optional[str]
    rag_context:    Optional[str]
    crag_score:     float

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
            "pending_confirmation": None,
            "confirmation_result":  None,
            "admin_token_verified": False,
            "admin_command":        None,
            "final_response": None,
            "rag_context":    None,
            "crag_score":     0.0,
        }