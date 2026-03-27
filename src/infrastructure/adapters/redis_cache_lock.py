# src/infrastructure/adapters/redis_cache_lock.py
from src.domain.ports.cache_lock import ICacheLock
from src.infrastructure.redis_client import get_redis_text

class RedisCacheLock(ICacheLock):

    async def is_locked(self, key: str) -> bool:
        r = get_redis_text()
        return bool(r.exists(f"lock:chat:{key}"))

    async def acquire(self, key: str, timeout: int = 90) -> bool:
        r = get_redis_text()
        # SET NX EX — atômico, sem race condition
        result = r.set(f"lock:chat:{key}", "1", nx=True, ex=timeout)
        return result is True

    async def release(self, key: str) -> None:
        r = get_redis_text()
        r.delete(f"lock:chat:{key}")