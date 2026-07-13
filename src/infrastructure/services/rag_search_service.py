"""
SHIM DE COMPATIBILIDADE — Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md.

RAGSearchService/QueryTransformService/DocumentChunkRepository foram movidos
para `src/agents/academic_knowledge/` (service.py + query_transform.py).
Remover na Fase 7.

`CalendarioService`, `EditalService`, `ContatosService`, `WikiCTICService`
NÃO foram portados: zero consumidores vivos (só referenciados por
`application/chain/oracle_chain.bak`, arquivo `.bak` nunca importado).
"""
from __future__ import annotations

from src.agents.academic_knowledge.query_transform import QueryTransformService, TransformedQuery
from src.agents.academic_knowledge.service import (
    DocumentChunkRepository,
    RAGSearchService,
    ToolResult,
    _normalizar,
)

__all__ = [
    "QueryTransformService",
    "TransformedQuery",
    "RAGSearchService",
    "ToolResult",
    "DocumentChunkRepository",
    "_normalizar",
]
