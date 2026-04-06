"""
src/memory/ports/long_term_port.py
------------------------------------
Interface da Memória de Longo Prazo (fatos sobre o usuário).

ESTRATÉGIAS SUPORTADAS:
  1. Quick Recall  → lista LIFO no Redis, busca por recência
  2. Semantic Recall → embedding + coseno, busca por relevância semântica
  3. Híbrida       → combina recência e relevância (MMR simplificado)

ESCALABILIDADE:
  A interface não sabe se o backend é Redis, Postgres ou qualquer outro.
  Para adicionar um novo backend, implementa ILongTermMemory e injeta.
"""
from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Fato:
    """
    Fato extraído sobre o usuário.
    Imutável após criação para segurança no pipeline.
    """
    texto: str
    user_id: str
    timestamp: int = field(default_factory=lambda: int(time.time()))
    relevance_score: float = 0.0  # score de similaridade semântica (0-1)
    source: str = "extractor"     # "extractor" | "manual" | "onboarding"

    def __post_init__(self):
        if not self.texto.strip():
            raise ValueError("Texto do fato não pode ser vazio")

    @property
    def hash_id(self) -> str:
        """Identificador único baseado no conteúdo (para dedup)."""
        return hashlib.md5(self.texto.lower().strip().encode()).hexdigest()[:16]

    def __str__(self) -> str:
        return self.texto

    def __hash__(self) -> int:
        return hash(self.hash_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Fato):
            return False
        return self.hash_id == other.hash_id


def fatos_para_string(fatos: list[Fato]) -> str:
    """Serializa lista de fatos para injeção no prompt."""
    if not fatos:
        return ""
    return "\n".join(f"- {f.texto}" for f in fatos)


class ILongTermMemory(ABC):
    """
    Porta para memória de longo prazo (fatos persistentes sobre o usuário).

    CONTRATO:
      - save()           → persiste um fato (idempotente — sem duplicatas)
      - save_batch()     → persiste múltiplos fatos
      - search_recent()  → N fatos mais recentes (Quick Recall)
      - search_semantic() → fatos relevantes por similaridade semântica
      - list_all()       → todos os fatos (para admin)
      - delete_all()     → apaga todos os fatos de um usuário
    """

    @abstractmethod
    def save(self, user_id: str, fato: Fato) -> bool:
        """
        Persiste um fato. Retorna True se novo, False se duplicado.
        NUNCA lança exceção.
        """

    @abstractmethod
    def save_batch(self, user_id: str, fatos: list[Fato]) -> int:
        """Persiste múltiplos fatos. Retorna quantidade de novos salvos."""

    @abstractmethod
    def search_recent(self, user_id: str, limit: int = 5) -> list[Fato]:
        """Retorna os N fatos mais recentes (Quick Recall — sem embedding)."""

    @abstractmethod
    def search_semantic(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.65,
    ) -> list[Fato]:
        """
        Retorna fatos relevantes por similaridade semântica.
        Fallback para search_recent() se embedding falhar.
        """

    @abstractmethod
    def list_all(self, user_id: str, limit: int = 50) -> list[Fato]:
        """Lista todos os fatos (para dashboard admin)."""

    @abstractmethod
    def delete_all(self, user_id: str) -> None:
        """Remove todos os fatos de um usuário."""

    def search_hybrid(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> list[Fato]:
        """
        Busca híbrida: combina semântica + recência.
        Implementação padrão — pode ser sobrescrita para otimização.

        MMR simplificado:
          1. Busca Top-2N por semântica
          2. Merge com Top-N por recência
          3. Dedup e retorna Top-N por relevância
        """
        semanticos = self.search_semantic(user_id, query, limit=limit * 2)
        recentes = self.search_recent(user_id, limit=limit)

        # Merge sem duplicatas preservando ordem de relevância
        vistos: set[str] = set()
        merged: list[Fato] = []
        for f in semanticos + recentes:
            if f.hash_id not in vistos:
                vistos.add(f.hash_id)
                merged.append(f)

        return merged[:limit]