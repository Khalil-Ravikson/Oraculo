# src/domain/entities/message.py
"""
Entidade de Mensagem WhatsApp — agnóstica de provedor.
Usada em todo o sistema sem importar SDKs externos.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass(frozen=True)
class Mensagem:
    """
    Representação imutável de uma mensagem WhatsApp recebida.
    Criada pelo DevGuard após validação do payload.
    """
    user_id:    str                    # Número normalizado (55989...)
    chat_id:    str                    # JID completo (55989...@s.whatsapp.net)
    body:       str                    # Texto da mensagem
    has_media:  bool = False
    msg_type:   str  = "conversation"
    push_name:  str  = ""             # Nome do contato no WhatsApp
    msg_key_id: str  = ""             # ID único da mensagem (dedup)


@dataclass(frozen=True)
class IdentidadeRica:
    """
    Identidade completa do usuário, montada pelo Porteiro PostgreSQL.
    Injetada no contexto do LangGraph sem precisar consultar o banco novamente.
    """
    user_id:      str
    chat_id:      str
    body:         str
    nome:         str
    role:         Literal["guest", "student", "admin", "professor", "servidor"]
    status:       Literal["ativo", "inativo", "pendente", "banido", "trancado"]
    is_admin:     bool = False
    curso:        Optional[str] = None
    periodo:      Optional[str] = None
    matricula:    Optional[str] = None
    centro:       Optional[str] = None
    has_media:    bool = False
    msg_type:     str  = "conversation"
    msg_key_id:   str  = ""

    @property
    def pode_usar_tools(self) -> bool:
        return self.status == "ativo" and self.role != "guest"

    @property
    def contexto_llm(self) -> dict:
        """Dicionário limpo para injeção no prompt do LLM."""
        return {
            k: v for k, v in {
                "nome":      self.nome,
                "curso":     self.curso,
                "periodo":   self.periodo,
                "matricula": self.matricula,
                "centro":    self.centro,
                "role":      self.role,
            }.items()
            if v is not None
        }

    def to_celery_dict(self) -> dict:
        """Serialização segura para envio ao Celery."""
        return {
            "user_id":    self.user_id,
            "chat_id":    self.chat_id,
            "body":       self.body,
            "nome":       self.nome,
            "role":       self.role,
            "status":     self.status,
            "is_admin":   self.is_admin,
            "curso":      self.curso,
            "periodo":    self.periodo,
            "matricula":  self.matricula,
            "centro":     self.centro,
            "has_media":  self.has_media,
            "msg_type":   self.msg_type,
            "msg_key_id": self.msg_key_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IdentidadeRica":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})