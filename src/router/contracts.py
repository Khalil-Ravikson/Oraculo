"""
src/router/contracts.py
========================
Contrato único de saída do Supervisor: nomes de rota válidos e o schema de
decisão (RouterDecision). Fonte única de verdade para "quais rotas existem" —
substitui a whitelist que antes vivia embutida no prompt de application/chain/planner.py.

Nenhuma lógica aqui, só dados/schema.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Rotas válidas ──────────────────────────────────────────────────────────────
ROTAS_VALIDAS = frozenset({
    "CALENDARIO", "EDITAL", "CONTATOS", "WIKI", "CRUD", "TICKET_ABERTURA", "GREETING", "GERAL", "MEDIA_DOWNLOAD", "SIGAA"
})

# ── Workers Celery válidos para o Planner ──────────────────────────────────────
# Fonte única de verdade (Fase 4): antes vivia duplicada como whitelist embutida
# no prompt de application/chain/planner.py (_SYSTEM_PLANNER) e como set solto
# em _planejar_com_pro. Agora agents/academic_knowledge/planning.py importa daqui.
VALID_WORKERS = frozenset({"rag_search", "synthesis", "greeting"})


@dataclass
class RouterDecision:
    rota: str
    confianca: float
    motivo: str
    cache_hit: bool
    cache_layer: str   # "exact" | "semantic" | "miss"
    latencia_ms: int
    dag_hint: dict     # dica para o Planner montar o DAG
