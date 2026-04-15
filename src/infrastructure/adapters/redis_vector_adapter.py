# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 2: src/infrastructure/adapters/redis_vector_adapter.py
# Responsabilidade: Persistência e busca de chunks de RAG via RedisVL.
# MUDANÇAS: redis-py manual → AsyncSearchIndex + HybridQuery nativo.
# ─────────────────────────────────────────────────────────────────────────────

"""
infrastructure/adapters/redis_vector_adapter.py — v4 (RedisVL HybridQuery)
===========================================================================
Implementa IVectorStorePort usando AsyncSearchIndex e HybridQuery do RedisVL.

ANTES (v3):
  - Duas buscas sequenciais (vetorial + textual) com redis-py manual
  - RRF calculado em Python puro após dois round-trips ao Redis
  - Serialização de embeddings manual com struct.pack

DEPOIS (v4):
  - HybridQuery do RedisVL: BM25 + Vector em UMA única query ao Redis
  - RRF nativo: rrf_constant=60, rrf_window=20 (mantém comportamento anterior)
  - AsyncSearchIndex: I/O totalmente não-bloqueante
  - Embedding gerado via CustomTextVectorizer (wraps Gemini async)
  - Salvar chunks: load() em pipeline — um round-trip para N documentos

COMPATIBILIDADE DE CAMPOS:
  Os campos content, source, doc_type, chunk_index, embedding são mantidos.
  O schema SVS-VAMANA requer re-ingestão se havia HNSW (ver redis_client.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from redisvl.index import AsyncSearchIndex
from redisvl.query import HybridQuery, VectorQuery
from redisvl.query.filter import Tag

from src.domain.ports.vector_store_port import IVectorStorePort
from src.infrastructure.redis_client import (
    VECTOR_DIM,
    get_async_chunks_index,
)

logger = logging.getLogger(__name__)

# Campos retornados por todas as queries de busca
_RETURN_FIELDS = ["content", "source", "doc_type", "chunk_index"]


class RedisVLVectorAdapter(IVectorStorePort):
    """
    Adapter de Vector Store usando RedisVL 0.17.0.

    Design:
      - Stateless: não guarda referência ao AsyncSearchIndex entre chamadas.
        O índice é criado por operação e desconectado no finally, para que o
        connection pool do redis-py subjacente gerencie as conexões.
      - Injecção de modelo de embeddings via construtor (não singleton global).
      - Todos os métodos são coroutines — sem asyncio.to_thread excepto no
        embed_query (CPU-bound legítimo do modelo).
    """

    def __init__(self, embeddings_model: Any) -> None:
        """
        Args:
            embeddings_model: Qualquer objecto com .embed_query(str) e
                              .embed_documents(list[str]) síncronos.
                              O adapter cuida do to_thread internamente.
        """
        self._emb = embeddings_model

    # ── Serialização de embeddings ─────────────────────────────────────────────

    async def _embed_query(self, text: str) -> list[float]:
        """Gera embedding de uma query. CPU-bound → executado em thread pool."""
        t0 = time.monotonic()
        try:
            vetor: list[float] = await asyncio.to_thread(
                self._emb.embed_query, text
            )
            ms = int((time.monotonic() - t0) * 1000)
            logger.debug(
                "🔢 [EMBED] query='%.50s' | dims=%d | %dms",
                text, len(vetor), ms,
            )
            return vetor
        except Exception as exc:
            logger.exception(
                "❌ [EMBED] Falha ao gerar embedding | texto='%s' | erro: %s",
                text[:80], exc,
            )
            raise

    async def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Gera embeddings em batch. CPU-bound → thread pool."""
        t0 = time.monotonic()
        try:
            vetores: list[list[float]] = await asyncio.to_thread(
                self._emb.embed_documents, texts
            )
            ms = int((time.monotonic() - t0) * 1000)
            logger.debug(
                "🔢 [EMBED BATCH] %d documentos | %dms (%.1fms/doc)",
                len(texts), ms, ms / max(len(texts), 1),
            )
            return vetores
        except Exception as exc:
            logger.exception(
                "❌ [EMBED BATCH] Falha em %d documentos | erro: %s",
                len(texts), exc,
            )
            raise

    # ── Persistência ───────────────────────────────────────────────────────────

    async def salvar_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """
        Persiste chunks no Redis com embeddings gerados em batch.

        Estratégia:
          1. Gera todos os embeddings em batch (1 chamada ao modelo)
          2. Usa AsyncSearchIndex.load() — pipeline Redis, 1 round-trip para N docs
          3. Desconecta no finally para libertar o socket

        Args:
            chunks: Lista de dicts com keys: chunk_id, content, source, doc_type,
                    metadata (dict com chunk_index).
        """
        if not chunks:
            return

        t0 = time.monotonic()
        logger.info("📦 [ADAPTER] Iniciando persistência de %d chunks...", len(chunks))

        # 1. Embeddings em batch
        textos = [c["content"] for c in chunks]
        embeddings = await self._embed_documents(textos)

        # 2. Monta documentos no formato esperado pelo AsyncSearchIndex
        docs: list[dict] = []
        for chunk, emb in zip(chunks, embeddings):
            meta = chunk.get("metadata", {})
            doc  = {
                "content":     chunk["content"],
                "source":      chunk["source"],
                "doc_type":    chunk["doc_type"],
                "chunk_index": meta.get("chunk_index", 0),
                "embedding":   emb,
            }
            # Chave determinística: source:chunk_id
            doc["_id"] = f"{chunk['source']}:{chunk['chunk_id']}"
            docs.append(doc)

        # 3. Persistência via RedisVL pipeline
        index = get_async_chunks_index()
        try:
            keys = await index.load(
                data=docs,
                id_field="_id",
                ttl=None,           # chunks são permanentes
            )
            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "✅ [ADAPTER] %d chunks persistidos em %dms | source=%s",
                len(keys), ms, chunks[0].get("source", "?"),
            )
        except Exception as exc:
            logger.exception(
                "❌ [ADAPTER] Falha ao persistir %d chunks: %s",
                len(chunks), exc,
            )
            raise
        finally:
            await index.disconnect()

    # ── Busca híbrida ──────────────────────────────────────────────────────────

    async def buscar_hibrido(
        self,
        query_text: str,
        k_vector: int = 8,
        k_text:   int = 8,
        source_filter: str | None = None,
        doc_type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca híbrida BM25 + Vector com RRF nativo do RedisVL.

        HybridQuery com rrf_constant=60 preserva o comportamento do RRF manual
        que já tínhamos, mas agora numa única query ao Redis (sem 2 round-trips).

        Args:
            query_text:       Texto da query para BM25 e embedding.
            k_vector:         Top-K da busca vectorial.
            k_text:           Top-K da busca BM25.
            source_filter:    Filtra por nome de ficheiro (ex: "edital_paes_2026.pdf").
            doc_type_filter:  Filtra por tipo de doc (ex: "calendario").

        Returns:
            Lista de dicts com {content, source, doc_type, chunk_index, rrf_score}.
            Ordenada por rrf_score decrescente.
        """
        t0 = time.monotonic()
        logger.debug(
            "🔍 [HYBRID] query='%.60s' | k_vec=%d k_text=%d | "
            "source=%s doc_type=%s",
            query_text, k_vector, k_text, source_filter, doc_type_filter,
        )

        # 1. Embedding da query
        vetor = await self._embed_query(query_text)

        # 2. Monta filtro declarativo
        filter_expr = self._build_filter(source_filter, doc_type_filter)

        # 3. HybridQuery com RRF nativo
        # num_results = max dos dois k (o RedisVL une internamente)
        num_results = max(k_vector, k_text)
        query = HybridQuery(
            text              = query_text,
            text_field_name   = "content",
            vector            = vetor,
            vector_field_name = "embedding",
            filter_expression = filter_expr,
            num_results       = num_results,
            return_fields     = _RETURN_FIELDS,
            # RRF config — mantém comportamento do algoritmo anterior
            rrf_constant      = 60,
            rrf_window        = max(k_vector, k_text) * 2,
            # BM25 config
            text_scorer       = "BM25STD",
            stopwords         = "portuguese",
        )

        # 4. Executa via AsyncSearchIndex
        index = get_async_chunks_index()
        try:
            results = await index.query(query)
        except Exception as exc:
            cause = type(exc).__name__
            logger.exception(
                "❌ [HYBRID] Busca falhou | idx=%s | causa=%s | query='%.60s' | erro: %s",
                "idx:rag:chunks", cause, query_text[:60], exc,
            )
            return []
        finally:
            await index.disconnect()

        ms = int((time.monotonic() - t0) * 1000)

        # 5. Normaliza resultados para o formato esperado pelos consumers
        resultados = self._normalizar_resultados(results)
        logger.info(
            "✅ [HYBRID] %d resultados | query='%.40s' | %dms",
            len(resultados), query_text, ms,
        )
        return resultados

    # ── Busca vetorial pura (para o SemanticCache e CRAG) ─────────────────────

    async def buscar_vetorial(
        self,
        query_text: str,
        k: int = 5,
        doc_type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca vectorial pura — usada pelo CRAG score e SemanticCache.
        Mais rápida que a híbrida (sem BM25), ideal para verificação de relevância.
        """
        t0 = time.monotonic()
        vetor = await self._embed_query(query_text)
        filter_expr = self._build_filter(None, doc_type_filter)

        query = VectorQuery(
            vector            = vetor,
            vector_field_name = "embedding",
            return_fields     = _RETURN_FIELDS,
            filter_expression = filter_expr,
            num_results       = k,
            return_score      = True,
        )

        index = get_async_chunks_index()
        try:
            results = await index.query(query)
        except Exception as exc:
            logger.exception(
                "❌ [VECTOR] Busca falhou | k=%d | erro: %s",
                k, exc,
            )
            return []
        finally:
            await index.disconnect()

        ms = int((time.monotonic() - t0) * 1000)
        logger.debug(
            "🔵 [VECTOR] %d resultados | %dms",
            len(results), ms,
        )
        return self._normalizar_resultados(results)

    # ── Interface IVectorStorePort (compatibilidade com código legado) ─────────

    async def salvar_chunks_legacy(self, chunks: list[dict[str, Any]]) -> None:
        """Alias para compatibilidade com testes existentes."""
        await self.salvar_chunks(chunks)

    async def buscar_contexto(
        self,
        query_text: str,
        k: int,
        source_filter: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Interface IVectorStorePort — retorna formato {vetorial:[], textual:[]}.
        Internamente usa HybridQuery mas mantém o contrato da porta.
        """
        resultados = await self.buscar_hibrido(
            query_text    = query_text,
            k_vector      = k,
            k_text        = k,
            source_filter = source_filter,
        )
        # Divide os resultados para manter compatibilidade com RetrieveContextUseCase
        metade = len(resultados) // 2
        return {
            "vetorial": [{"id": r["id"], "content": r["content"], "source": r["source"]}
                         for r in resultados[:metade or len(resultados)]],
            "textual":  [{"id": r["id"], "content": r["content"], "source": r["source"]}
                         for r in resultados[metade:]],
        }

    # ── Utilitários internos ───────────────────────────────────────────────────

    @staticmethod
    def _build_filter(
        source: str | None,
        doc_type: str | None,
    ):
        """
        Constrói FilterExpression declarativa do RedisVL.
        Evita construção manual de strings @source:{...} — mais legível e seguro.
        """
        expr = None
        if source:
            expr = Tag("source") == source
        if doc_type:
            dt_filter = Tag("doc_type") == doc_type
            expr = dt_filter if expr is None else (expr & dt_filter)
        return expr

    @staticmethod
    def _normalizar_resultados(
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Normaliza o output do RedisVL para o formato esperado pelos consumers.
        O RedisVL retorna dicts com chaves do schema + score fields.
        """
        normalizados = []
        for doc in results:
            normalizados.append({
                "id":          doc.get("id", ""),
                "content":     doc.get("content", ""),
                "source":      doc.get("source", ""),
                "doc_type":    doc.get("doc_type", ""),
                "chunk_index": doc.get("chunk_index", 0),
                # HybridQuery usa combined_score; VectorQuery usa vector_distance
                "rrf_score":   float(doc.get("combined_score",
                               doc.get("vector_distance", 0.0))),
            })
        # Ordena por score decrescente (maior = mais relevante)
        normalizados.sort(key=lambda x: x["rrf_score"], reverse=True)
        return normalizados