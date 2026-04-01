import logging
from redis.asyncio import Redis, ConnectionPool
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# Singleton para o Pool de Conexões
_async_pool = None

async def get_async_redis() -> Redis:
    """
    Gere o pool de ligações assíncronas ao Redis Stack.
    Configurado para otimizar a RAM disponível (16GB).
    """
    global _async_pool
    
    if _async_pool is None:
        logger.info("⏳ A abrir autoestrada assíncrona para o Redis Stack...")
        _async_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=False,  # Necessário para os bytes dos vectores
            max_connections=30,      # Equilíbrio para não sobrecarregar a CPU
            socket_connect_timeout=5,
            socket_timeout=10
        )
    
    return Redis(connection_pool=_async_pool)