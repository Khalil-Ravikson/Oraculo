"""
src/application/runtime/contracts.py
================================================================================
Contrato de retorno neutro do runtime de orquestração — extraído de
`dispatcher.py` (Plano A / Fase 2, Parte C: pré-requisito da aposentadoria do
`dispatcher.py` legado).

`OSResult` é o contrato devolvido por
`application/orchestration/entrypoint.py::processar` — hoje o único
orquestrador (ADR 0008). Os dois dispatchers que a versão anterior desta
docstring citava foram deletados na Fase 3; extrair este contrato para cá foi
justamente o pré-requisito que permitiu removê-los, porque
`domain_services/sigaa/auth_flow.py` e os funis de chamado importavam `OSResult` de
dentro do dispatcher legado. Corrigido em 2026-09-09 (item A9).
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
