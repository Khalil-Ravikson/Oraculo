"""
src/memory/ports/working_memory_port.py
----------------------------------------
Interface da Memória de Trabalho (Working Memory).

RESPONSABILIDADE ÚNICA:
  Gerenciar o histórico da conversa ATIVA com sliding window e token budget.
  Não mistura estado de menu, fatos de longo prazo nem contexto de usuário.

PADRÃO:
  IWorkingMemory é a porta. RedisWorkingMemory é o adapter.
  O LangGraph injeta IWorkingMemory nos nodes — nunca acessa Redis diretamente.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


Papel = Literal["user", "assistant"]


@dataclass(frozen=True)
class ConversationTurn:
    """
    Um turno imutável da conversa.
    Imutabilidade evita mutação acidental no pipeline do LangGraph.
    """
    role: Papel
    content: str
    timestamp: int = 0

    def truncated(self, max_chars: int) -> "ConversationTurn":
        """Retorna versão truncada sem alterar o original."""
        if len(self.content) <= max_chars:
            return self
        return ConversationTurn(
            role=self.role,
            content=self.content[:max_chars] + "…",
            timestamp=self.timestamp,
        )


@dataclass
class HistoricoCompactado:
    """
    Histórico pronto para injeção no prompt do LLM.
    Resultado de get_historico() — imutável após criação.
    """
    turns: list[ConversationTurn] = field(default_factory=list)
    texto_formatado: str = ""
    total_chars: int = 0
    turns_incluidos: int = 0

    @classmethod
    def vazio(cls) -> "HistoricoCompactado":
        return cls()

    @property
    def tem_historico(self) -> bool:
        return self.turns_incluidos > 0


class IWorkingMemory(ABC):
    """
    Porta para memória de trabalho (histórico da conversa ativa).

    CONTRATO:
      - add_turn()       → persiste um turno (nunca lança exceção)
      - get_historico()  → retorna histórico compactado dentro do budget
      - clear()          → apaga toda a sessão
      - get_recent_turns() → retorna N turnos para o extrator de fatos

    THREAD SAFETY: implementações devem ser seguras para uso concorrente.
    """

    @abstractmethod
    def add_turn(self, session_id: str, role: Papel, content: str) -> None:
        """
        Adiciona um turno ao histórico.
        NUNCA lança exceção — em caso de falha, loga e ignora.
        """

    @abstractmethod
    def get_historico(self, session_id: str) -> HistoricoCompactado:
        """
        Retorna histórico dentro do token budget, garantindo:
          1. Máximo N turns (sliding window)
          2. Máximo M chars total (token budget)
          3. Sempre começa com turno "user"
          4. Pares user/assistant preservados
        """

    @abstractmethod
    def clear(self, session_id: str) -> None:
        """Remove todo o histórico da sessão."""

    @abstractmethod
    def get_recent_turns(self, session_id: str, n: int = 6) -> list[ConversationTurn]:
        """Retorna os N turnos mais recentes (para o extrator de fatos)."""

    @abstractmethod
    def set_signal(self, session_id: str, key: str, value: str) -> None:
        """Armazena sinal de contexto da sessão (rota, tool usada, etc.)."""

    @abstractmethod
    def get_signals(self, session_id: str) -> dict[str, str]:
        """Retorna todos os sinais de contexto da sessão."""