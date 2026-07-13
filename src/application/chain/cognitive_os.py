"""
SHIM DE COMPATIBILIDADE — Fase 3 do PLANO_REFATORACAO_SUPERVISOR.md.

O CognitiveOS foi decomposto em:
  - `src/application/runtime/dispatcher.py` (entry point `processar()`,
    `OSResult`, dispatch de workers Celery, polling de resposta final)
  - `src/agents/sigaa/auth_flow.py` (HITL de autenticação SIGAA)
  - `src/capabilities/persistence/redis_state.py` (IO Redis cru)

Este módulo permanece como re-export fino porque `processar`,
`_despachar_workers` e `_aguardar_resposta_final` têm consumidores externos
vivos além de `process_message_task.py`: `api/chain_sse.py`,
`api/routers/web/hub.py` e `api/routers/admin/eval_api.py` (modo síncrono de
debug). Remover na Fase 7, junto com os demais shims.
"""
from __future__ import annotations

from src.application.runtime.dispatcher import (
    OSResult,
    _aguardar_resposta_final,
    _despachar_workers,
    processar,
)

__all__ = ["OSResult", "processar", "_despachar_workers", "_aguardar_resposta_final"]
