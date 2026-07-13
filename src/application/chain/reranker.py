"""
SHIM DE COMPATIBILIDADE — Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md.

O reranker foi movido para `src/capabilities/rag/reranker.py` (probe de rede
manual removida — ver docstring desse módulo). Remover na Fase 7.
"""
from __future__ import annotations

from src.capabilities.rag.reranker import get_reranker, rerank

__all__ = ["get_reranker", "rerank"]
