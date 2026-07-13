"""
SHIM DE COMPATIBILIDADE — Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md.

O Planner foi movido para `src/agents/academic_knowledge/planning.py`.
Remover na Fase 7.
"""
from __future__ import annotations

from src.agents.academic_knowledge.planning import (
    ExecutionPlan,
    ExecutionPlanSchema,
    PlanStepSchema,
    StepArgsSchema,
    criar_plano,
)

__all__ = [
    "ExecutionPlan",
    "ExecutionPlanSchema",
    "PlanStepSchema",
    "StepArgsSchema",
    "criar_plano",
]
