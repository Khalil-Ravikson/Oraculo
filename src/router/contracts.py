"""
src/router/contracts.py
========================
Contrato único de saída do Supervisor: nomes de rota válidos e o schema de
decisão (`RouterDecision`). Fonte única de "quais rotas existem".

Nenhuma lógica aqui, só dados/schema.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Rotas válidas ──────────────────────────────────────────────────────────────
ROTAS_VALIDAS = frozenset({
    "CALENDARIO", "EDITAL", "CONTATOS", "WIKI", "CRUD", "TICKET_ABERTURA",
    "GREETING", "GERAL", "MEDIA_DOWNLOAD", "SIGAA", "CHECK_STATUS", "ESCALAR_HUMANO",
})

# ── Rotas que nunca fazem sentido cachear (SemanticCache) ──────────────────────
# Fonte de verdade em runtime é a coluna `cacheavel` do `route_registry`. Este
# frozenset é o baseline hardcoded (espelha `route_registry._DEFAULTS`;
# `test_route_registry` trava a paridade) e serve consumidores de teste/doc.
ROTAS_SEM_CACHE = frozenset({
    "SIGAA", "MEDIA_DOWNLOAD", "GREETING", "CRUD", "CHECK_STATUS", "ESCALAR_HUMANO",
})


@dataclass
class RouterDecision:
    rota: str
    confianca: float
    motivo: str
    cache_hit: bool
    cache_layer: str   # "exact" | "semantic" | "miss"
    latencia_ms: int
    dag_hint: dict     # {"doc_type", "k_vector", "k_text"} pro RAG — ver supervisor._dag_hint_para_rota
