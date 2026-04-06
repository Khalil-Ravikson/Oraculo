"""
src/domain/ports/tool_ports.py
--------------------------------
Interfaces (Ports) para todas as ferramentas do Oráculo.

DESIGN:
  Cada tool tem uma interface aqui e uma implementação em
  src/infrastructure/services/ ou src/domain/tools/.
  Os nodes do LangGraph recebem as interfaces — nunca as implementações.
  Isso permite trocar GLPI real por mock, SMTP por SendGrid, etc.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Result types (imutáveis, serializáveis para o state do LangGraph)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolResult:
    """Resultado padrão de qualquer tool."""
    ok: bool
    message: str
    data: dict = field(default_factory=dict)
    error: str = ""

    @classmethod
    def success(cls, message: str, data: dict | None = None) -> "ToolResult":
        return cls(ok=True, message=message, data=data or {})

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, message="Operação falhou.", error=error)

    def to_agent_str(self) -> str:
        """Serializa para string que o LangGraph passa ao LLM."""
        import json
        return json.dumps({"ok": self.ok, "message": self.message, **self.data}, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Interfaces por domínio
# ─────────────────────────────────────────────────────────────────────────────

class IGLPIService(ABC):
    """Interface para o sistema de chamados GLPI."""

    @abstractmethod
    async def abrir_chamado(
        self,
        titulo: str,
        descricao: str,
        local: str = "Não informado",
        urgencia: str = "media",
        user_email: str = "",
    ) -> ToolResult: ...

    @abstractmethod
    async def consultar_fila(self, user_email: str = "") -> ToolResult: ...

    @abstractmethod
    async def atualizar_chamado(self, chamado_id: int, status: str) -> ToolResult: ...


class IEmailService(ABC):
    """Interface para envio de e-mail institucional."""

    @abstractmethod
    async def enviar(
        self,
        destinatario: str,
        assunto: str,
        corpo: str,
        remetente: str = "",
        template_id: str | None = None,
        template_vars: dict | None = None,
    ) -> ToolResult: ...


class IRAGSearchService(ABC):
    """Interface para busca nos documentos RAG (roteada por doc_type)."""

    @abstractmethod
    async def buscar(self, query: str, doc_type: str, source_filter: str | None = None) -> ToolResult: ...


class ICalendarioService(ABC):
    """Interface para consulta ao calendário acadêmico."""

    @abstractmethod
    async def consultar(self, query: str) -> ToolResult: ...

    @abstractmethod
    async def proximos_eventos(self, dias: int = 7) -> ToolResult: ...


class IEditalService(ABC):
    """Interface para consulta ao edital PAES."""

    @abstractmethod
    async def consultar(self, query: str) -> ToolResult: ...


class IContatosService(ABC):
    """Interface para consulta ao guia de contatos UEMA."""

    @abstractmethod
    async def consultar(self, query: str) -> ToolResult: ...


class IWikiCTICService(ABC):
    """Interface para consulta à Wiki do CTIC."""

    @abstractmethod
    async def consultar(self, query: str) -> ToolResult: ...


class INotificationService(ABC):
    """Interface para notificações proativas (WhatsApp/Email)."""

    @abstractmethod
    async def notificar(self, chat_id: str, mensagem: str) -> ToolResult: ...