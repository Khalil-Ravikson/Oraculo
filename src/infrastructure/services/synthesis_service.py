"""
SHIM DE COMPATIBILIDADE — Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md.

SynthesisService/SynthesisResult foram movidos e consolidados com a
implementação (antes duplicada) de `application/workers/worker_synthesis.py`
em `src/agents/academic_knowledge/synthesis.py` — ver docstring desse módulo
para a mudança de comportamento deliberada (overrides administrativos agora
valem para o tráfego real). Remover na Fase 7.
"""
from __future__ import annotations

from src.agents.academic_knowledge.synthesis import SynthesisResult, SynthesisService

__all__ = ["SynthesisResult", "SynthesisService"]
