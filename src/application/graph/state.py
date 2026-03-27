from typing import TypedDict, Annotated, Literal, Optional, Dict, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class OracleState(TypedDict):
    # ── Identidade (Injetada pelo Porteiro/PostgreSQL) ────────────
    user_phone:   str
    user_id:      str
    user_name:    str
    user_role:    Literal["guest", "student", "admin"]
    user_status:  str
    user_context: Dict[str, Any]  # Ex: {"matricula": "123", "curso": "Eng. Civil", "periodo": 11}

    # ── Conversa ──────────────────────────────────────────────────
    messages:      Annotated[list[BaseMessage], add_messages]
    current_input: str

    # ── Roteamento ────────────────────────────────────────────────
    route:     Literal["rag", "crud", "greeting", "blocked", "respond_only"]
    tool_name: Optional[str]
    tool_args: Optional[Dict[str, Any]]

    # ── Human-in-the-Loop (Regra 3) ───────────────────────────────
    pending_confirmation: Optional[str]
    confirmation_result:  Optional[Literal["confirmed", "cancelled", "pending"]]

    # ── Output ────────────────────────────────────────────────────
    final_response: Optional[str]
    rag_context:    Optional[str]
    crag_score:     float