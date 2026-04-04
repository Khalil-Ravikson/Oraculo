# src/application/graph/state.py
"""
OracleState v3 — Estado completo do LangGraph.

MUDANÇAS vs versão anterior:
  + is_admin: bool             → detectado pelo AdminInterceptorNode
  + admin_token_verified: bool → double-check de token para ações admin
  + maintenance_mode: bool     → modo manutenção global
  + system_prompt_key: str     → chave Redis do prompt dinâmico
  + audit_action: str          → ação a registar no audit log
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class OracleState(dict):
    """
    Estado do grafo LangGraph.

    Usa herança de dict em vez de TypedDict para compatibilidade
    com o RedisSaver (serialização JSON nativa).

    CAMPOS PRINCIPAIS:
    ──────────────────
      Identidade:
        user_phone, user_id, user_name, user_role, user_status
        user_context (curso, período, matrícula)
        is_admin → flag que ativa o AdminInterceptorNode

      Conversa:
        messages      → histórico (gerido pelo LangGraph add_messages)
        current_input → mensagem atual do usuário

      Roteamento:
        route      → "rag" | "crud" | "greeting" | "admin" | "maintenance"
        tool_name  → nome da tool a executar
        tool_args  → argumentos da tool

      HITL (Human-in-the-Loop):
        pending_confirmation → pergunta ao usuário antes de executar
        confirmation_result  → "confirmed" | "cancelled" | "pending"
        hitl_token           → token gerado para validação admin

      Admin:
        admin_token_verified → True após double-check de senha
        admin_command        → comando extraído (ex: "BAN:user123")
        audit_action         → string para o IAuditLog

      Output:
        final_response → resposta final ao usuário
        rag_context    → contexto recuperado (para auditoria)
        crag_score     → score de qualidade do RAG
    """

    # ── Factories ─────────────────────────────────────────────────────────────

    @classmethod
    def from_identity(
        cls,
        identity: dict,
        messages: list | None = None,
    ) -> "OracleState":
        """Cria estado a partir do dict de identidade do Porteiro."""
        return cls({
            # Identidade
            "user_phone":    identity.get("user_id", ""),
            "user_id":       identity.get("user_id", ""),
            "user_name":     identity.get("nome", "Usuário"),
            "user_role":     identity.get("role", "guest"),
            "user_status":   identity.get("status", "pendente"),
            "user_context":  {
                "curso":      identity.get("curso"),
                "periodo":    identity.get("periodo"),
                "matricula":  identity.get("matricula"),
                "centro":     identity.get("centro"),
            },
            "is_admin":      identity.get("is_admin", False),

            # Conversa
            "messages":      messages or [],
            "current_input": identity.get("body", ""),

            # Roteamento
            "route":         "rag",
            "tool_name":     None,
            "tool_args":     None,

            # HITL
            "pending_confirmation": None,
            "confirmation_result":  None,
            "hitl_token":           None,

            # Admin
            "admin_token_verified": False,
            "admin_command":        None,
            "audit_action":         None,

            # Output
            "final_response": None,
            "rag_context":    None,
            "crag_score":     0.0,
        })

    # ── Helpers de leitura ────────────────────────────────────────────────────

    @property
    def eh_admin(self) -> bool:
        return bool(self.get("is_admin", False))

    @property
    def pode_executar_tool(self) -> bool:
        """True somente se o HITL foi confirmado."""
        return self.get("confirmation_result") == "confirmed"

    @property
    def tem_confirmacao_pendente(self) -> bool:
        return (
            self.get("pending_confirmation") is not None
            and self.get("confirmation_result") not in ("confirmed", "cancelled")
        )