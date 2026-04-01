import logging
from src.infrastructure.database.redis_connection import get_async_redis

logger = logging.getLogger(__name__)

async def acquire_lock(lock_name: str, acquire_timeout: int = 10, lock_timeout: int = 10) -> bool:
    """
    Adquire uma trava no Redis de forma assíncrona para evitar processamento duplicado.
    """
    try:
        r = await get_async_redis()
        # NX = Só define se não existir | EX = Expira em X segundos
        lock_key = f"lock:{lock_name}"
        return await r.set(lock_key, "locked", ex=lock_timeout, nx=True)
    except Exception as e:
        logger.error(f"❌ Erro ao adquirir lock {lock_name}: {e}")
        return False

async def release_lock(lock_name: str):
    """
    Libera a trava no Redis.
    """
    try:
        r = await get_async_redis()
        lock_key = f"lock:{lock_name}"
        await r.delete(lock_key)
    except Exception as e:
        logger.error(f"❌ Erro ao liberar lock {lock_name}: {e}")