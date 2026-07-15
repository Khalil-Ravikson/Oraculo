# tests/unit/agents/test_can_execute_toggle.py
"""
Confirma que os 4 agentes registrados respeitam o liga/desliga do painel
/hub/agents (capabilities/persistence/agent_config.py) via can_execute().

`set_agent_enabled` é async desde a Sprint 2 Fase 5 (dual-write best-effort
no catálogo Postgres) — `can_execute()`/`is_agent_enabled` continuam
síncronos e 100% Redis nesta fase (flip para Postgres-first é a Fase 6).
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

    assert agent.can_execute(ctx) is True  # ativo por padrão

    await set_agent_enabled(redis, agent.name, False)
    assert agent.can_execute(ctx) is False

    await set_agent_enabled(redis, agent.name, True)
    assert agent.can_execute(ctx) is True
