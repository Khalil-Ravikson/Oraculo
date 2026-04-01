import os
import logging
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Variável global para manter o Pool de Conexões vivo (Singleton)
_redis_async_client = None

async def get_async_redis() -> redis.Redis:
    """
    Retorna uma conexão assíncrona única (Singleton) com o Redis.
    Perfeita para lidar com as múltiplas requisições paralelas do RAG.
    """
    global _redis_async_client
    
    if _redis_async_client is None:
        # Puxa a URL do .env ou usa o padrão do Docker
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6380/0")
        
        logger.info(f"🔌 Abrindo nova autoestrada assíncrona para o Redis: {redis_url}")
        
        # IMPORTANTE: decode_responses=False porque nós precisamos trafegar 
        # os bytes crus dos vetores (struct.pack) para o RediSearch!
        _redis_async_client = redis.from_url(
            redis_url, 
            decode_responses=False,
            max_connections=100  # Aguenta muita concorrência do Celery/FastAPI
        )
        
    return _redis_async_client

async def close_async_redis():
    """Fecha a conexão elegantemente ao desligar o servidor."""
    global _redis_async_client
    if _redis_async_client is not None:
        await _redis_async_client.aclose()
        _redis_async_client = None
        logger.info("🔌 Conexão assíncrona com Redis encerrada.")