# src/application/graph/state.py
from __future__ import annotations
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages

class OracleState(TypedDict):
    # ── Identidade (preenchida antes de entrar no grafo) ──────────
    user_phone:   str
    user_id:      str
    user_name:    str
    user_role:    Literal["guest", "student", "admin"]
    user_status:  str
    user_context: dict  # matricula, curso, período — injetado no prompt

    # ── Conversa ──────────────────────────────────────────────────
    messages:      Annotated[list, add_messages]
    current_input: str

    # ── Roteamento ────────────────────────────────────────────────
    route:     Literal["rag", "crud", "greeting", "blocked"]
    tool_name: str | None
    tool_args: dict | None

    # ── Human-in-the-Loop ─────────────────────────────────────────
    # Pergunta enviada ao usuário aguardando confirmação
    pending_confirmation: str | None
    # Resposta do usuário: "confirmed" | "cancelled" | "pending"
    confirmation_result:  Literal["confirmed", "cancelled", "pending"] | None

    # ── Output ────────────────────────────────────────────────────
    final_response: str | None
    rag_context:    str | None
    crag_score:     float