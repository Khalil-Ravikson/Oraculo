# tests/unit/agents/test_can_execute_toggle.py
"""
Confirma que os 4 agentes registrados respeitam o liga/desliga do painel
/hub/agents (capabilities/persistence/agent_config.py) via can_execute().

`can_execute()` é async desde a Sprint 2 Fase 6 (Postgres-first com fallback
Redis dentro de `is_agent_enabled`).
"""
import pytest

from src.agents.base import AgentContext
from src.agents.academic_knowledge.service import AcademicKnowledgeAgent
from src.agents.sigaa.service import SigaaAgent
from src.agents.conversation.registration import ConversationAgent
from src.agents.tickets.service import TicketAgent
from src.capabilities.persistence.agent_config import set_agent_enabled


class FakeRedis:
    def __init__(self):
        self.db = {}

    def get(self, key):
        return self.db.get(key)

    def set(self, key, value):
        self.db[key] = value


AGENTES = [AcademicKnowledgeAgent, SigaaAgent, ConversationAgent, TicketAgent]


@pytest.mark.asyncio
@pytest.mark.parametrize("AgentCls", AGENTES)
async def test_can_execute_respeita_toggle_desativado(AgentCls):
    redis = FakeRedis()
    agent = AgentCls()
    ctx = AgentContext(session_id="s1", redis=redis)

    assert await agent.can_execute(ctx) is True  # ativo por padrão

    await set_agent_enabled(redis, agent.name, False)
    assert await agent.can_execute(ctx) is False

    await set_agent_enabled(redis, agent.name, True)
    assert await agent.can_execute(ctx) is True
