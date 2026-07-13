"""
src/agents/registry.py
=========================
AgentRegistry — o router nunca importa uma classe de agente diretamente,
sempre resolve por nome aqui (ver seção 0.2 do PLANO_REFATORACAO_SUPERVISOR.md).

Analogia com `src/application/workers/registry.py` (WorkerRegistry): mesmo
padrão de registro central, mas para agentes em vez de Celery tasks.

Nesta fase (Fase 2) o registry existe mas está vazio — nenhum agente
concreto foi criado ainda. Cada fase seguinte registra o seu:
  Fase 3/5 → agents/sigaa/service.py     → registry.register(SigaaAgent())
  Fase 4   → agents/academic_knowledge/  → registry.register(AcademicKnowledgeAgent())
  Fase 6   → agents/conversation/, agents/tickets/
"""
from __future__ import annotations

import logging

from src.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if agent.name in self._agents:
            logger.warning("⚠️  [AGENT REGISTRY] Sobrescrevendo agente já registrado: '%s'", agent.name)
        self._agents[agent.name] = agent
        logger.debug("✅ [AGENT REGISTRY] Agente registrado: '%s'", agent.name)

    def resolve(self, name: str) -> BaseAgent:
        agent = self._agents.get(name)
        if agent is None:
            raise KeyError(
                f"Agente '{name}' não registrado. Disponíveis: {list(self._agents.keys())}"
            )
        return agent

    def all(self) -> list[BaseAgent]:
        return list(self._agents.values())


registry = AgentRegistry()
