"""
SHIM DE COMPATIBILIDADE — Fase 2 do PLANO_REFATORACAO_SUPERVISOR.md.

O Semantic Router foi movido para `src/router/supervisor.py` (+ `src/router/
llm_fallback.py` para a camada Gemini, + `src/router/contracts.py` para o
schema de decisão). Este módulo passa a ser um re-export fino para não
quebrar imports existentes (`cognitive_os.py`, `hub.py`, testes).

Remover na Fase 7, junto com os demais shims de `application/routing/`.
"""
from __future__ import annotations

from src.router.contracts import ROTAS_VALIDAS, RouterDecision
from src.router.llm_fallback import RoutingDecision, _classificar_com_flash, _regex_fallback
from src.router.supervisor import (
    _dag_hint_para_rota,
    _heuristica_basica,
    _obter_intent_config,
    _regex_rapido,
    rotear,
)

__all__ = [
    "ROTAS_VALIDAS",
    "RouterDecision",
    "RoutingDecision",
    "_classificar_com_flash",
    "_regex_fallback",
    "_dag_hint_para_rota",
    "_heuristica_basica",
    "_obter_intent_config",
    "_regex_rapido",
    "rotear",
]
