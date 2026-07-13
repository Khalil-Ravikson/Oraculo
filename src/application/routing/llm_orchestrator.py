"""
SHIM DE COMPATIBILIDADE — Fase 2 do PLANO_REFATORACAO_SUPERVISOR.md.

O LLMOrchestrator foi mesclado em `src/router/llm_fallback.py` (elimina o
"terceiro cérebro" paralelo — agora os dois fallbacks LLM do Supervisor vivem
no mesmo arquivo). Remover na Fase 7, junto com os demais shims de
`application/routing/`.
"""
from __future__ import annotations

from src.router.llm_fallback import ACTIONS, OrchestratorDecision, orchestrate

__all__ = ["ACTIONS", "OrchestratorDecision", "orchestrate"]
