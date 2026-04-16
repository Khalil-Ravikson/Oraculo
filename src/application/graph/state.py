"""
application/graph/state.py — OracleState v4
============================================
Estado do LangGraph com factory `from_identity` para compatibilidade
com process_message_task.py e simulate_web_chat.py.
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional
from typing_extensions import TypedDict


class OracleState(TypedDict, total=False):
    """
    Estado partilhado entre todos os nós do LangGraph.
    Campos com Annotated[list, operator.add] são acumulativos (append).
    Todos os outros campos usam last-write-wins.
    """
    # ── Identificação ─────────────────────────────────────────────────────────
    session_id:   str
    user_id:      str          # alias de session_id (legado)
    user_phone:   str          # JID completo WhatsApp
    is_admin:     bool

    # ── Contexto rico do aluno (carregado pelo Porteiro PostgreSQL) ────────────
    user_context: dict         # {nome, curso, periodo, matricula, centro}
    user_name:    str          # primeiro nome para personalização
    user_role:    str          # "admin" | "estudante" | "publico"
    user_status:  str          # "ativo" | "inativo" | "banido"
    curso:        str
    periodo:      str
    centro:       str
    matricula:    str

    # ── Input da conversa ─────────────────────────────────────────────────────
    user_message:  str         # texto normalizado do webhook
    current_input: str         # alias de user_message (legado)
    has_media:     bool
    historico:     str         # histórico compactado para o prompt

    # ── Roteamento ────────────────────────────────────────────────────────────
    route:      str            # nó alvo ("retrieve_node", "crud_node", etc.)
    crag_score: float

    # ── RAG ───────────────────────────────────────────────────────────────────
    rag_context:      str      # contexto formatado para o LLM
    contexto_rag:     Annotated[list[dict], operator.add]
    crag_aprovado:    bool
    query_reescrita:  str
    relevance:        str      # "yes" | "no" (CRAG loop)
    loop_count:       int

    # ── Geração ───────────────────────────────────────────────────────────────
    resposta_final:  str
    final_response:  str       # alias de resposta_final (legado)
    messages:        list      # lista de mensagens LangChain (legado)
    cache_hit:       bool

    # ── CRUD / HITL ───────────────────────────────────────────────────────────
    tool_name:            str
    tool_args:            dict
    tool_result:          Optional[dict]
    pending_confirmation: Optional[str]   # pergunta de confirmação ao usuário
    confirmation_result:  Optional[str]   # "confirmed" | "cancelled" | "pending"
    awaiting_hitl:        bool

    # ── Admin ─────────────────────────────────────────────────────────────────
    admin_command:        Optional[str]
    admin_token_verified: bool

    # ── Observabilidade ───────────────────────────────────────────────────────
    trace_id:     str
    node_timings: Annotated[list[dict], operator.add]


# ─────────────────────────────────────────────────────────────────────────────
# Factory function (TypedDict não suporta classmethods — padrão de monkey-patch)
# ─────────────────────────────────────────────────────────────────────────────

def _oracle_state_from_identity(identity: dict) -> OracleState:
    """
    Constrói o estado inicial do grafo a partir da IdentidadeRica.
    Chamado no process_message_task e no simulador web.
    """
    return {
        "session_id":  identity.get("user_id",      identity.get("sender_phone", "")),
        "user_id":     identity.get("user_id",      identity.get("sender_phone", "")),
        "user_phone":  identity.get("chat_id",      ""),
        "is_admin":    identity.get("is_admin",     False),
        "user_context": {
            "nome":      identity.get("nome",      ""),
            "curso":     identity.get("curso"),
            "periodo":   identity.get("periodo"),
            "matricula": identity.get("matricula"),
            "centro":    identity.get("centro"),
        },
        "user_name":   (identity.get("nome") or "").split()[0] if identity.get("nome") else "",
        "user_role":   identity.get("role",   "publico"),
        "user_status": identity.get("status", "ativo"),
        "curso":       identity.get("curso",      "") or "",
        "periodo":     identity.get("periodo",    "") or "",
        "centro":      identity.get("centro",     "") or "",
        "matricula":   identity.get("matricula",  "") or "",
        "user_message":  identity.get("body", ""),
        "current_input": identity.get("body", ""),
        "has_media":     identity.get("has_media", False),
        "messages":      [],
        "loop_count":    0,
        "node_timings":  [],
        "awaiting_hitl": False,
    }


# Monkey-patch: permite OracleState.from_identity(identity) em todo o codebase
OracleState.from_identity = staticmethod(_oracle_state_from_identity)  # type: ignore[attr-defined]