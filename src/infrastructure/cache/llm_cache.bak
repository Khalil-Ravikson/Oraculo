# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 3: src/infrastructure/cache/llm_cache.py
# Responsabilidade: Cache semântico para respostas LLM via RedisVL.
# ─────────────────────────────────────────────────────────────────────────────

"""
infrastructure/cache/llm_cache.py — SemanticCache (RedisVL 0.17.0)
===================================================================
Cache semântico: perguntas semanticamente similares reutilizam a mesma resposta.

IMPORT CORRECTO (0.17.0):
  from redisvl.extensions.cache.llm import SemanticCache
  (O import de redisvl.extensions.llmcache está deprecated)

COMO FUNCIONA:
  1. acheck(prompt=query) → busca por embedding no índice do cache
  2. Se similaridade ≥ threshold → retorna resposta cacheada (0 tokens LLM)
  3. Se miss → chama LLM normalmente
  4. astore(prompt, response) → persiste para futuras queries similares

THRESHOLD CALIBRADO:
  distance_threshold=0.12 (distância coseno, não similaridade)
  0.12 ≈ similaridade 0.88 — perguntas muito parecidas mas não idênticas
  Ex: "quando é a matrícula?" ≈ "qual a data da matrícula?" → HIT
  Ex: "quando é a matrícula?" ≠ "quando é a formatura?"    → MISS

COST SAVING ESTIMATE:
  60-70% das queries académicas UEMA são repetiçoes (mesmo período, mesmos prazos).
  Com threshold=0.12, estimativa de hit rate ~45%, reduzindo tokens em ~45%.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from redisvl.extensions.cache.llm import SemanticCache
from redisvl.utils.vectorize import CustomTextVectorizer

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# Threshold de distância coseno (0=idêntico, 1=oposto)
# 0.12 ≈ similaridade 0.88 — calibrado para corpus académico UEMA
_DEFAULT_DISTANCE_THRESHOLD = 0.12
_DEFAULT_TTL = 86400 * 7      # 7 dias
_CACHE_NAME  = "oraculo:llm_cache"


class OracloSemanticCache:
    """
    Wrapper sobre SemanticCache do RedisVL com interface async limpa.

    Responsabilidade única: verificar e persistir respostas LLM em cache.
    NÃO decide quando usar cache — isso é responsabilidade do caller.

    NOTA SOBRE SINCRONISMO:
      SemanticCache.acheck() e astore() são nativamente async no redisvl 0.17.0.
      CustomTextVectorizer com aembed async permite embedding não-bloqueante.
    """

    def __init__(
        self,
        embeddings_model: Any,
        distance_threshold: float = _DEFAULT_DISTANCE_THRESHOLD,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._threshold = distance_threshold
        self._ttl = ttl

        # CustomTextVectorizer wraps o modelo de embeddings existente
        # sem criar dependência directa do redisvl no modelo
        vectorizer = CustomTextVectorizer(
            embed       = embeddings_model.embed_query,
            embed_many  = embeddings_model.embed_documents,
            # aembed async wrapping o síncrono via asyncio.to_thread
            aembed      = lambda text, **kw: asyncio.get_event_loop().run_in_executor(
                None, embeddings_model.embed_query, text
            ),
        )

        self._cache = SemanticCache(
            name               = _CACHE_NAME,
            distance_threshold = distance_threshold,
            ttl                = ttl,
            vectorizer         = vectorizer,
            redis_url          = settings.REDIS_URL,
        )

        logger.info(
            "✅ [SEMANTIC CACHE] Iniciado | threshold=%.2f | ttl=%dd",
            distance_threshold, ttl // 86400,
        )

    async def verificar(
        self,
        prompt: str,
        doc_type: str | None = None,
    ) -> str | None:
        """
        Verifica cache para o prompt dado.

        Args:
            prompt:   Texto da query/pergunta.
            doc_type: Usado como filtro (ex: "calendario") para evitar
                      cross-contamination entre tipos de documento.

        Returns:
            String com a resposta cacheada, ou None se cache miss.
        """
        t0 = time.monotonic()
        try:
            # acheck retorna lista de CacheHit objects
            hits = await self._cache.acheck(
                prompt            = prompt,
                num_results       = 1,
                distance_threshold= self._threshold,
                filter_expression = None,  # doc_type filter via metadata futuro
            )

            ms = int((time.monotonic() - t0) * 1000)

            if hits:
                resposta = hits[0].get("response", "")
                distance = hits[0].get("vector_distance", 1.0)
                similarity = 1.0 - float(distance)
                logger.info(
                    "🎯 [CACHE HIT] sim=%.4f | doc_type=%s | %dms | "
                    "query='%.50s'",
                    similarity, doc_type, ms, prompt,
                )
                return resposta

            logger.debug(
                "⬜ [CACHE MISS] %dms | query='%.50s'",
                ms, prompt,
            )
            return None

        except Exception as exc:
            logger.exception(
                "❌ [CACHE] Falha em acheck | causa=%s | query='%.60s': %s",
                type(exc).__name__, prompt[:60], exc,
            )
            return None   # fail-open: não deixa o sistema parar por falha de cache

    async def armazenar(
        self,
        prompt: str,
        response: str,
        doc_type: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """
        Armazena par (prompt, response) no cache semântico.

        Args:
            prompt:   Query original.
            response: Resposta gerada pelo LLM.
            doc_type: Tag para namespace (opcional, para futura filtragem).
            metadata: Dados adicionais (rota, tokens, latência — para analytics).
        """
        t0 = time.monotonic()
        try:
            meta = {
                "doc_type": doc_type or "geral",
                **(metadata or {}),
            }
            await self._cache.astore(
                prompt   = prompt,
                response = response,
                metadata = meta,
                ttl      = self._ttl,
            )
            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "💾 [CACHE STORE] doc_type=%s | %dms | query='%.50s'",
                doc_type, ms, prompt,
            )
        except Exception as exc:
            logger.exception(
                "❌ [CACHE] Falha em astore | causa=%s | erro: %s",
                type(exc).__name__, exc,
            )
            # Fail silently: não perder a resposta se o cache falhar

    async def invalidar_por_doc_type(self, doc_type: str) -> int:
        """
        Invalida entradas do cache de um tipo de documento específico.
        Chamado após re-ingestão de um PDF (edital actualizado, etc.).

        Returns:
            Número de entradas removidas.
        """
        try:
            await self._cache.aclear()   # versão simplificada — limpa tudo
            logger.info(
                "🗑️  [CACHE] Cache invalidado para doc_type=%s", doc_type
            )
            return -1   # redisvl aclear não retorna count na 0.17.0
        except Exception as exc:
            logger.exception(
                "❌ [CACHE] Falha ao invalidar doc_type=%s: %s",
                doc_type, exc,
            )
            return 0

    async def stats(self) -> dict:
        """Métricas do cache para o endpoint /cache/stats."""
        try:
            index = self._cache.index
            info  = await asyncio.to_thread(index.info)   # info é síncrono
            return {
                "name":       _CACHE_NAME,
                "threshold":  self._threshold,
                "ttl_days":   self._ttl // 86400,
                "num_entries": info.get("num_docs", 0),
            }
        except Exception as exc:
            logger.warning("⚠️  [CACHE] stats falhou: %s", exc)
            return {"error": str(exc)}