"""
src/infrastructure/semantic_cache.py
------------------------------------
Semantic Cache por Rota para o Oráculo UEMA.
Usa Redis para armazenar respostas de LLM (sintetizadas) e
as reaproveita baseando-se na similaridade de cosseno (> 0.92) da query.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from src.infrastructure.redis_client import get_redis_text
from src.rag.embeddings import get_embeddings

logger = logging.getLogger(__name__)

class SemanticCache:
    """
    Cache Semântico por Rota.
    Guarda respostas e as recupera se a similaridade de cosseno com a nova query for >= threshold.
    """
    def __init__(self, threshold: float = 0.92):
        self.threshold = threshold
        self._redis = get_redis_text()
        self._emb = get_embeddings()

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        import math
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    async def get(self, query: str, rota: str) -> dict | None:
        """
        Verifica se há um cache hit para a query na rota específica.
        """
        try:
            # CPU-bound: gerar embedding da query
            query_emb = await asyncio.to_thread(self._emb.embed_query, query)
            
            pattern = f"semcache:{rota}:*"
            cursor = 0
            best_match = None
            best_score = 0.0
            
            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    data = self._redis.hgetall(key)
                    if not data:
                        continue
                        
                    emb_str = data.get(b"embedding") or data.get("embedding")
                    if isinstance(emb_str, bytes):
                        emb_str = emb_str.decode('utf-8')
                        
                    if not emb_str:
                        continue
                        
                    cached_emb = json.loads(emb_str)
                    score = self._cosine_similarity(query_emb, cached_emb)
                    
                    if score >= self.threshold and score > best_score:
                        best_score = score
                        resp_str = data.get(b"response") or data.get("response")
                        if isinstance(resp_str, bytes):
                            resp_str = resp_str.decode('utf-8')
                        best_match = resp_str
                        
                if cursor == 0:
                    break
                    
            if best_match:
                logger.info("✅ Semantic Cache hit! (score=%.3f, rota=%s)", best_score, rota)
                return json.loads(best_match)
                
        except Exception as e:
            logger.warning("⚠️  Falha no SemanticCache.get: %s", e)
            
        return None

    async def set(self, query: str, rota: str, response: dict, ttl: int = 3600) -> None:
        """
        Armazena a resposta no cache.
        """
        if not query or not rota:
            return

        try:
            query_emb = await asyncio.to_thread(self._emb.embed_query, query)
            import hashlib
            query_hash = hashlib.md5(query.encode()).hexdigest()
            key = f"semcache:{rota}:{query_hash}"
            
            mapping = {
                "query": query,
                "embedding": json.dumps(query_emb),
                "response": json.dumps(response, ensure_ascii=False)
            }
            
            self._redis.hset(key, mapping=mapping)
            self._redis.expire(key, ttl)
            logger.debug("💾 Semantic Cache armazenado (rota=%s, ttl=%ds)", rota, ttl)
            
        except Exception as e:
            logger.warning("⚠️  Falha no SemanticCache.set: %s", e)
