"""
src/rag/query/protocols.py
--------------------------
Ports (interfaces) para estratégias de transformação de query.

DESIGN:
  Cada estratégia é um objeto que implementa IQueryStrategy.
  O QueryTransformer recebe uma lista de estratégias na construção
  e as aplica em pipeline. Adicionar uma nova estratégia = criar uma
  classe que implemente IQueryStrategy e registrá-la — zero mudanças
  no código existente.

  IQueryStrategy.transform() é síncrona para compatibilidade com o
  pipeline Celery (asyncio.run já está no nível da task).
  Para chamadas LLM async, use asyncio.run() internamente ou
  injete um executor.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class RawQuery:
    """Query original do utilizador antes de qualquer transformação."""
    text: str
    user_id: str = ""
    session_id: str = ""
    fatos_usuario: list[str] = field(default_factory=list)
    historico_recente: list[dict] = field(default_factory=list)
    doc_type: str | None = None          # hint de roteamento (pode ser None)


@dataclass
class TransformedQuery:
    """
    Query após transformação. Pode conter múltiplas variantes para
    busca paralela (Multi-Query / HyDE).
    """
    original: str
    primary: str                          # query principal a usar no BM25+Vector
    variants: list[str] = field(default_factory=list)   # queries extras (RAG Fusion)
    hypothetical_doc: str = ""            # HyDE: documento hipotético gerado
    step_back: str = ""                   # versão mais genérica (fallback)
    keywords: list[str] = field(default_factory=list)
    strategy_used: str = "passthrough"
    was_transformed: bool = False

    @property
    def all_queries(self) -> list[str]:
        """Retorna todas as queries para busca paralela."""
        queries = [self.primary]
        queries.extend(v for v in self.variants if v and v != self.primary)
        return queries


@runtime_checkable
class IQueryStrategy(Protocol):
    """
    Contrato para estratégias de transformação de query.
    
    REGRA: transform() NUNCA lança exceção — em caso de falha retorna
    TransformedQuery com a query original (graceful degradation).
    """

    @property
    def name(self) -> str:
        """Identificador único da estratégia (para logs e métricas)."""
        ...

    def transform(self, query: RawQuery) -> TransformedQuery:
        """
        Transforma a query. Chamado síncronamente pelo pipeline.
        Implementações que precisam de LLM devem usar asyncio.run()
        internamente ou um cliente síncrono (ex: google-genai sync).
        """
        ...

    def should_apply(self, query: RawQuery) -> bool:
        """
        Decide se esta estratégia deve ser aplicada para a query dada.
        Permite curto-circuito eficiente (ex: não aplicar HyDE a queries
        já técnicas de 2+ palavras-chave específicas).
        """
        ...


class AbstractQueryStrategy(ABC):
    """Classe base opcional para implementações concretas."""

    def should_apply(self, query: RawQuery) -> bool:
        """Por padrão, sempre aplica. Sobrescreva para lógica específica."""
        return True

    @abstractmethod
    def transform(self, query: RawQuery) -> TransformedQuery:
        ...