"""
src/capabilities/rag/reranker.py
===================================
Singleton Cross-Encoder para re-ranking local (ex `application/chain/reranker.py`,
Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md).
Modelo leve (~90MB): cross-encoder/ms-marco-MiniLM-L-6-v2

MUDANÇA NESTA FASE: removida a probe de rede manual (`urllib.request.urlopen`
contra huggingface.co) que existia só para decidir se tentava o download
online antes de cair pro modo offline — o próprio `CrossEncoder(...)` já
lança exceção se a rede estiver indisponível, capturada pelos blocos
try/except abaixo. A probe era uma chamada de rede solta dentro do que
deveria ser só carregamento de modelo (apontado na auditoria original).
Resiliência preservada: mesma cadeia de fallback (online principal → online
alternativo → local offline → desativa via Redis flag).
"""
from __future__ import annotations
import logging
import os
from functools import lru_cache

os.environ["HF_HOME"] = "/home/oraculo/.cache/huggingface"

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_reranker():
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        if r.get("reranker:status") == "disabled":
            logger.warning("⚠️ Reranker desativado via Redis flag. Ignorando.")
            return None
    except Exception:
        pass

    try:
        from sentence_transformers import CrossEncoder

        try:
            # a) Tentativa 1: Online principal
            os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "5"
            model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
            logger.info("✅ CrossEncoder carregado (Online Principal)")
            return model
        except Exception as e:
            logger.warning("⚠️ Tentativa 1 (Principal) falhou: %s", e)
            try:
                # b) Tentativa 2: Online secundário
                model = CrossEncoder("BAAI/bge-reranker-base", max_length=512)
                logger.info("✅ CrossEncoder carregado (Online Alternativo)")
                return model
            except Exception as e2:
                logger.warning("⚠️ Tentativa 2 (Alternativo) falhou: %s", e2)

        # c) Tentativa 3: Local offline
        try:
            os.environ["HF_HUB_OFFLINE"] = "1"
            model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512, local_files_only=True)
            logger.info("✅ CrossEncoder carregado (Local/Offline)")
            return model
        except Exception as e3:
            logger.error("❌ Tentativa 3 (Local) falhou: %s", e3)
            # d) Desativar reranker globalmente
            try:
                from src.infrastructure.redis_client import get_redis_text
                r = get_redis_text()
                r.set("reranker:status", "disabled")
                logger.error("❌ Reranker desabilitado globalmente no Redis. Retornando chunks crus.")
            except Exception:
                pass
            return None

    except ImportError:
        logger.warning("⚠️ sentence-transformers não instalado — re-ranking desativado")
        return None

async def rerank(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        if r.get("reranker:status") == "disabled":
            return chunks[:top_k]
    except Exception:
        pass

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
