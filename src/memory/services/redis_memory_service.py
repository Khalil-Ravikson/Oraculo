"""
src/memory/services/redis_memory_service.py
5-layer MemoryService usando redis.asyncio.
Totalmente compatível com FastAPI e LangGraph sem bloquear o event loop.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from typing import Any
import redis.asyncio as aioredis
from src.infrastructure.settings import settings

_TTL = 1800  # 30 min

@dataclass
class MemorySnapshot:
    conversation: list[dict]
    operational:  dict
    task_history: dict
    user:         dict


class CognitiveMemoryService:
    """
    Layer 1 – Conversation  → Redis List  (chat:{sid})
    Layer 2 – Operational   → Redis JSON  (op:{sid})
    Layer 3 – Task History  → Redis Hash  (task_hist:{sid})
    Layer 4 – User Memory   → Redis Hash  (user_mem:{uid})
    Layer 5 – Knowledge     → RAG (não tocado aqui)
    """

    def __init__(self, redis_async_client: aioredis.Redis, window: int = 10):
        self._r = redis_async_client
        self._window = window

    # ── Layer 1: Conversation ──────────────────────────────────────────────────

    async def add_turn(self, session_id: str, role: str, content: str) -> None:
        key = f"chat:{session_id}"
        entry = json.dumps({"role": role, "content": content, "ts": int(time.time())},
                           ensure_ascii=False)
        await self._r.rpush(key, entry)
        await self._r.ltrim(key, -(self._window * 2), -1)
        await self._r.expire(key, _TTL)

    async def get_conversation(self, session_id: str) -> list[dict]:
        raw = await self._r.lrange(f"chat:{session_id}", 0, -1) or []
        turns = []
        for item in raw:
            try:
                turns.append(json.loads(item))
            except Exception:
                pass
        return turns

    async def format_history(self, session_id: str) -> str:
        turns = await self.get_conversation(session_id)
        lines = []
        for t in turns:
            prefix = "Aluno" if t["role"] == "user" else "Assistente"
            lines.append(f"{prefix}: {t['content'][:300]}")
        return "\n".join(lines)

    # ── Layer 2: Operational ───────────────────────────────────────────────────

    async def set_operational(self, session_id: str, data: dict) -> None:
        await self._r.setex(f"op:{session_id}", _TTL,
                            json.dumps(data, ensure_ascii=False))

    async def get_operational(self, session_id: str) -> dict:
        raw = await self._r.get(f"op:{session_id}")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    async def clear_operational(self, session_id: str) -> None:
        await self._r.delete(f"op:{session_id}")

    # ── Layer 3: Task History ──────────────────────────────────────────────────

    async def save_task_result(self, session_id: str, worker: str, result: str) -> None:
        key = f"task_hist:{session_id}"
        await self._r.hset(key, mapping={
            "last_worker": worker,
            "last_result": result[:500],
            "ts": str(int(time.time())),
        })
        await self._r.expire(key, _TTL)

    async def get_task_history(self, session_id: str) -> dict:
        return await self._r.hgetall(f"task_hist:{session_id}") or {}

    # ── Layer 4: User Memory ───────────────────────────────────────────────────

    async def set_user_memory(self, user_id: str, data: dict) -> None:
        key = f"user_mem:{user_id}"
        await self._r.hset(key, mapping={k: str(v) for k, v in data.items()})
        await self._r.expire(key, 86400 * 7)

    async def get_user_memory(self, user_id: str) -> dict:
        return await self._r.hgetall(f"user_mem:{user_id}") or {}

    # ── Snapshot completo (para injetar no Synthesis) ──────────────────────────

    async def snapshot(self, session_id: str, user_id: str) -> MemorySnapshot:
        return MemorySnapshot(
            conversation=await self.get_conversation(session_id),
            operational=await self.get_operational(session_id),
            task_history=await self.get_task_history(session_id),
            user=await self.get_user_memory(user_id),
        )


import weakref
import asyncio

# Usamos um WeakKeyDictionary em vez de uma variável global simples.
# Isto garante que:
# 1. Cada Event Loop (FastAPI vs Celery Task) tem a sua própria ligação Redis.
# 2. Quando o Celery destrói o Event Loop no fim da task, a ligação ao Redis 
#    é apagada automaticamente (evitando Memory Leaks).
_instances = weakref.WeakKeyDictionary()

def get_cognitive_memory() -> CognitiveMemoryService:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Fallback de segurança: Se for chamado fora de uma função async,
        # cria uma ligação isolada e descartável (sem caching).
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return CognitiveMemoryService(client)

    if loop not in _instances:
        # Se for a primeira vez neste Event Loop, criamos a ligação
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50
        )
        _instances[loop] = CognitiveMemoryService(client)

    return _instances[loop]
