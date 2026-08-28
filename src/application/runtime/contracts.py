"""
src/application/runtime/contracts.py
================================================================================
Contrato de retorno neutro do runtime de orquestração — extraído de
`dispatcher.py` (Plano A / Fase 2, Parte C: pré-requisito da aposentadoria do
`dispatcher.py` legado).

`OSResult` é o dict-contrato devolvido tanto por `dispatcher.py::processar`
quanto por `dispatcher_langgraph.py::processar`, e consumido por
`agents/sigaa/auth_flow.py`, `agents/tickets/ticket_flow.py`,
`agents/tickets/crud_tool.py`. Vivia em `dispatcher.py`, o que forçava esses
módulos a importar do dispatcher legado — impedindo removê-lo.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OSResult:
    answer: str
    plan_id: str
    rota: str
    cache_hit: bool
    total_ms: int
    status: str   # "ok" | "timeout" | "error" | "hitl_pending"
    error: str = ""
    action_buttons: list = field(default_factory=list)
