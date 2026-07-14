"""
src/agents/bootstrap.py
==========================
Registro explícito dos agentes no `AgentRegistry` (ver agents/registry.py,
Fase 2). Chamado uma vez no startup do FastAPI (`src/main.py`).

Por que explícito e não autodiscovery (ainda): autodiscovery via `pkgutil`
(o mesmo padrão de `application/workers/registry.py::_autodiscover_workers`)
é a evolução natural apontada na revisão do plano — mas com só 2 agentes
reais existindo até agora, introduzir esse mecanismo agora seria
especulativo. Este módulo é o único lugar que precisa mudar quando isso for
feito (troca o corpo da função, mantém a assinatura `register_all_agents()`).
"""
from __future__ import annotations

import logging

from src.agents.registry import registry

logger = logging.getLogger(__name__)

_REGISTERED = False


def register_all_agents() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    from src.agents.academic_knowledge.service import AcademicKnowledgeAgent
    from src.agents.sigaa.service import SigaaAgent

    registry.register(AcademicKnowledgeAgent())
    registry.register(SigaaAgent())

    _REGISTERED = True
    logger.info("✅ [AGENT REGISTRY] Agentes registrados: %s", [a.name for a in registry.all()])
