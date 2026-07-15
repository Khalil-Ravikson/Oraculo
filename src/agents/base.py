"""
src/agents/base.py
=====================
Contrato comum a todo agente + AgentContext (ver seção 0.2 do
PLANO_REFATORACAO_SUPERVISOR.md).

Regra de assinatura para TODO agente, presente e futuro: sempre
`execute(context: AgentContext)`, nunca `execute(user)`, `execute(redis)`,
`execute(message)` soltos. O AgentContext concentra tudo que um agente pode
precisar, injetado pelo dispatcher/runtime — o agente nunca importa Redis,
Postgres ou o provider LLM diretamente.

Nesta fase (Fase 2) só o mecanismo é criado. Nenhum agente concreto ainda
implementa este contrato — isso acontece progressivamente nas Fases 3
(agents/sigaa/auth_flow.py), 4 (agents/academic_knowledge/), 5
(agents/sigaa/service.py) e 6 (agents/conversation/, agents/tickets/).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class AgentContext:
    """Objeto único injetado em todo agente — nunca parâmetros soltos."""
    session_id: str
    identity: dict = field(default_factory=dict)        # matrícula, papel: aluno/servidor/professor
    permissions: list[str] = field(default_factory=list)
    conversation: dict = field(default_factory=dict)      # histórico da conversa corrente
    memory: Any = None                                     # memória de curto/longo prazo já carregada
    redis: Any = None                                       # client injetado, nunca importado direto por agente
    postgres: Any = None                                     # sessão/engine injetada
    llm: Any = None                                           # provider LLM configurado (Gemini etc.)
    config: dict = field(default_factory=dict)                 # overrides administrativos (admin:*)


@dataclass
class AgentResponse:
    answer: str
    status: str = "ok"          # "ok" | "error" | "hitl_pending"
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class BaseAgent(Protocol):
    name: str
    description: str
    permissions: list[str]

    def can_execute(self, context: AgentContext) -> bool: ...
    async def execute(self, context: AgentContext) -> AgentResponse: ...


class AgentEnabledMixin:
    """`can_execute()` comum a todo agente concreto (Sprint 2, Fase 1).

    Elimina a duplicação idêntica que existia em `academic_knowledge/service.py`,
    `sigaa/service.py`, `conversation/registration.py` e `tickets/service.py`.
    Não substitui `BaseAgent` (Protocol) — só fornece a implementação default
    para quem herdar dele.
    """

    name: str

    def can_execute(self, context: AgentContext) -> bool:
        from src.capabilities.persistence.agent_config import is_agent_enabled
        return is_agent_enabled(context.redis, self.name)
