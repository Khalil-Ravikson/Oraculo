"""
SHIM DE COMPATIBILIDADE — Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md.

O RegistrationFunnel foi movido para `src/agents/conversation/registration.py`
(SQL cru em `capabilities/persistence/registration_repository.py`, envio de
botões em `capabilities/messaging/evolution_tool.py`). Remover na Fase 7,
junto com os demais shims de `application/routing/`.
"""
from __future__ import annotations

from src.agents.conversation.registration import RegistrationFunnel

__all__ = ["RegistrationFunnel"]
