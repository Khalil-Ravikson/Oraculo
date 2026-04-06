"""
src/memory/ports/fact_extractor_port.py
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from .long_term_port import Fato
from .working_memory_port import ConversationTurn


class IFactExtractor(ABC):
    """
    Porta para extração de fatos a partir do histórico de conversas.
    
    O extrator analisa os últimos N turnos e extrai fatos persistentes
    sobre o usuário (curso, turno, intenções recorrentes, etc.).
    
    Implementações:
      LLMFactExtractor  → usa LLM para extração inteligente
      RegexFactExtractor → extração por padrões locais (0 tokens)
    """

    @abstractmethod
    def extract(
        self,
        user_id: str,
        turns: list[ConversationTurn],
    ) -> list[Fato]:
        """
        Extrai fatos dos turnos fornecidos.
        NUNCA lança exceção — retorna lista vazia em caso de falha.
        """