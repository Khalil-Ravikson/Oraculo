"""
src/capabilities/persistence/redis_state.py
==============================================
Estado conversacional (HITL, cache de resultados, stream de respostas finais)
extraído de `application/chain/cognitive_os.py` (Fase 3 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.2 ponto 4).

Capability "burra": só encapsula as chamadas Redis já existentes (mesmas
chaves, mesmos TTLs). Nenhuma decisão de negócio aqui — quem decide o que
fazer com o estado é `application/runtime/dispatcher.py` e
`agents/sigaa/auth_flow.py`.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

# Mesmas constantes de cognitive_os.py — chaves/TTLs preservados.
STREAM_FINAL_RESPONSES = "oraculo:stream:final_responses"
RESULTS_CACHE_PREFIX   = "plan:results:"
RESULTS_TTL            = 120
HITL_SESSION_TTL       = 300
AUTH_TOKEN_TTL         = 300


async def get_hitl_session(r: Any, session_id: str) -> dict | None:
    raw = await asyncio.to_thread(r.get, f"hitl:session:{session_id}")
    if not raw:
        return None
    return json.loads(raw if isinstance(raw, str) else raw.decode())


async def set_hitl_session(r: Any, session_id: str, data: dict, ttl: int = HITL_SESSION_TTL) -> None:
    await asyncio.to_thread(r.setex, f"hitl:session:{session_id}", ttl, json.dumps(data, ensure_ascii=False))


async def delete_hitl_session(r: Any, session_id: str) -> None:
    await asyncio.to_thread(r.delete, f"hitl:session:{session_id}")


async def set_auth_token(r: Any, token: str, data: dict, ttl: int = AUTH_TOKEN_TTL) -> None:
    await asyncio.to_thread(r.setex, f"hitl:auth_token:{token}", ttl, json.dumps(data))


async def has_sigaa_session(r: Any, session_id: str) -> bool:
    return bool(await asyncio.to_thread(r.exists, f"sigaa:session:{session_id}"))


async def get_result_cache(r: Any, plan_id: str, step: str) -> dict | None:
    raw = await asyncio.to_thread(r.get, f"{RESULTS_CACHE_PREFIX}{plan_id}:{step}")
    if not raw:
        return None
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:
        return None


async def mark_plan_processing(r: Any, plan_id: str, ttl: int = 120) -> None:
    await asyncio.to_thread(r.setex, f"plan:status:{plan_id}", ttl, "processing")
