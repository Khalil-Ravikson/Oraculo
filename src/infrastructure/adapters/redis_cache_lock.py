import logging
import redis.asyncio as redis
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# Pega a URL do Redis (tenta minúsculo ou maiúsculo dependendo de como está no seu settings.py)
REDIS_URL = getattr(settings, "redis_url", getattr(settings, "REDIS_URL", "redis://localhost:6380/0"))

# Inicializa o cliente Redis assíncrono para o Lock
redis_client = redis.from_url(str(REDIS_URL), decode_responses=True)

async def acquire_lock(phone: str, ttl_seconds: int = 60) -> bool:
    """
    Tenta criar uma chave no Redis. 
    Se a chave já existir, retorna False (O usuário está travado).
    Se não existir, cria com um TTL (Time-to-Live) e retorna True.
    """
    try:
        lock_key = f"lock:whatsapp:{phone}"
        # nx=True garante que só seta se não existir (evita race conditions)
        # ex=ttl_seconds define a expiração automática
        is_acquired = await redis_client.set(lock_key, "1", nx=True, ex=ttl_seconds)
        return bool(is_acquired)
    except Exception as e:
        logger.error(f"Erro ao tentar adquirir lock no Redis para {phone}: {e}")
        # Em caso de queda do Redis, retornamos True para não travar o bot inteiro
        return True

async def release_lock(phone: str):
    """Libera a trava manualmente."""
    try:
        lock_key = f"lock:whatsapp:{phone}"
        await redis_client.delete(lock_key)
    except Exception as e:
        logger.error(f"Erro ao liberar lock no Redis para {phone}: {e}")