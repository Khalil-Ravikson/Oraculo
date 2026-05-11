"""
Singleton Cross-Encoder para re-ranking local.
Modelo leve (~90MB): cross-encoder/ms-marco-MiniLM-L-6-v2
"""
from __future__ import annotations
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_reranker():
    try:
        from sentence_transformers import CrossEncoder
        model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
        logger.info("✅ CrossEncoder carregado")
        return model
    except ImportError:
        logger.warning("⚠️  sentence-transformers não instalado — re-ranking desativado")
        return None
        
async def rerank(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    model = get_reranker()
    if not model or not chunks:
        return chunks[:top_k]

    pairs = [(query, c.get("content", "")[:512]) for c in chunks]
    
    # CPU-bound → thread pool, não bloqueia o event loop
    import asyncio
    scores = await asyncio.to_thread(model.predict, pairs)

    for chunk, score in zip(chunks, scores):
        chunk["rerank_score"] = float(score)

    return sorted(chunks, key=lambda c: c["rerank_score"], reverse=True)[:top_k]