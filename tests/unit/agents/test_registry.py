# tests/unit/agents/test_registry.py
"""
Testes do mecanismo de AgentRegistry criado na Fase 2 do
PLANO_REFATORACAO_SUPERVISOR.md. Nenhum agente concreto existe ainda —
este teste usa um dublê mínimo só para validar o mecanismo de registro.
"""
import pytest
from dataclasses import dataclass, field

from src.agents.base import AgentContext, AgentResponse
from src.agents.registry import AgentRegistry


@dataclass
class _DummyAgent:
    name: str = "dummy"
    description: str = "agente de teste"
    permissions: list = field(default_factory=list)

    def can_execute(self, context: AgentContext) -> bool:
        return True

    async def execute(self, context: AgentContext) -> AgentResponse:
        return AgentResponse(answer=f"ok:{context.session_id}")


def test_register_and_resolve():
    reg = AgentRegistry()
    reg.register(_DummyAgent())

    agent = reg.resolve("dummy")
    assert agent.name == "dummy"
    assert reg.all() == [agent]


def test_resolve_unregistered_raises():
    reg = AgentRegistry()
    with pytest.raises(KeyError):
        reg.resolve("inexistente")


@pytest.mark.asyncio
async def test_agent_execute_receives_single_context():
    reg = AgentRegistry()
    reg.register(_DummyAgent())

    ctx = AgentContext(session_id="abc123")
    resposta = await reg.resolve("dummy").execute(ctx)

    assert resposta.answer == "ok:abc123"
    assert resposta.status == "ok"
