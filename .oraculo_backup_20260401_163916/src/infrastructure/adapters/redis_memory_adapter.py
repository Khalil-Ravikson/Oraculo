import logging
import json
from typing import List, Optional
from src.infrastructure.database.redis_connection import get_async_redis

logger = logging.getLogger(__name__)

class RedisMemoryAdapter:
    """
    Responsável exclusivo pela persistência do contexto do utilizador e memória de longo prazo.
    """
    def __init__(self):
        self.prefix_work = "mem:work:"
        self.prefix_facts = "mem:facts:"

    async def get_working_memory(self, session_id: str) -> dict:
        r = await get_async_redis()
        # Usamos o comando direto do redis-py assíncrono
        data = await r.hgetall(f"{self.prefix_work}{session_id}")
        # Como a conexão base é bytes, descodificamos aqui
        return {k.decode(): v.decode() for k, v in data.items()} if data else {}

    async def set_working_memory(self, session_id: str, dados: dict, ttl: int = 1800):
        r = await get_async_redis()
        key = f"{self.prefix_work}{session_id}"
        if dados:
            await r.hset(key, mapping=dados)
            await r.expire(key, ttl)

    async def add_fact(self, user_id: str, fact: str):
        """Guarda factos importantes (ex: curso do aluno) para o Long-Term Memory."""
        r = await get_async_redis()
        key = f"{self.prefix_facts}{user_id}"
        await r.lpush(key, fact)
        await r.ltrim(key, 0, 49) # Mantém apenas os 50 factos mais recentes