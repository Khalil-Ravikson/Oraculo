"""
SHIM DE COMPATIBILIDADE — Fase 2 do PLANO_REFATORACAO_SUPERVISOR.md.

O MessageRouter (Gatekeeper) foi movido para `src/router/gatekeeper.py`.
Remover na Fase 7, junto com os demais shims de `application/routing/`.
"""
from __future__ import annotations

from src.router.gatekeeper import DispatchTarget, MessageRouter, RouterDecision

__all__ = ["DispatchTarget", "MessageRouter", "RouterDecision"]
