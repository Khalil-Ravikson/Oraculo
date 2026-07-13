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
    "CALENDARIO", "EDITAL", "CONTATOS", "WIKI", "CRUD", "GREETING", "GERAL", "MEDIA_DOWNLOAD", "SIGAA"
})


@dataclass
class RouterDecision:
    rota: str
    confianca: float
    motivo: str
    cache_hit: bool
    cache_layer: str   # "exact" | "semantic" | "miss"
    latencia_ms: int
    dag_hint: dict     # dica para o Planner montar o DAG
