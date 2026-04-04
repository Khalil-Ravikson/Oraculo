import redis.asyncio as redis
from src.infrastructure.settings import config

# Cliente global do Redis
redis_client = redis.from_url(config.redis_url, decode_responses=True)

async def acquire_lock(phone: str, ttl_seconds: int = 60) -> bool:
    """
    Tenta criar uma chave no Redis. 
    Se a chave já existir, retorna False (O usuário está travado).
    Se não existir, cria com um TTL (Time-to-Live) e retorna True.
    """
    lock_key = f"lock:whatsapp:{phone}"
    # nx=True garante que só seta se não existir (evita race conditions)
    # ex=ttl_seconds define a expiração automática para não travar para sempre
    is_acquired = await redis_client.set(lock_key, "1", nx=True, ex=ttl_seconds)
    return bool(is_acquired)

async def release_lock(phone: str):
    """Libera a trava manualmente."""
    lock_key = f"lock:whatsapp:{phone}"
    await redis_client.delete(lock_key)