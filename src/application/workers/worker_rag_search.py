"""
src/application/workers/worker_rag_search.py
=============================================
Worker RAG Search — consome eventos do Redis Stream, executa busca híbrida
via RedisVLVectorAdapter e publica resultado de volta no Stream.

STREAM EVENTS:
  Consome: oraculo:stream:rag_search_requests
    Fields: plan_id, session_id, step_id, doc_type, k, query, query_embedding_b64

  Publica: oraculo:stream:step_results
    Fields: plan_id, session_id, step_id, worker, status, result_json

MÉTRICAS:
  oraculo_rag_search_latency_ms (histogram)
  oraculo_rag_search_chunks_returned (histogram)
  oraculo_event_latency_ms{worker} (histogram)
"""
from __future__ import annotations

import base64
import json
import logging
import struct
import time

from prometheus_client import Counter, Histogram

from src.infrastructure.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Métricas ──────────────────────────────────────────────────────────────────
_RAG_LATENCY = Histogram(
    "oraculo_rag_search_latency_ms",
    "Latência da busca RAG em ms",
    buckets=[10, 25, 50, 100, 250, 500, 1000],
)
_CHUNKS_RETURNED = Histogram(
    "oraculo_rag_search_chunks_returned",
    "Chunks retornados por busca",
    buckets=[0, 1, 3, 5, 8, 10, 15],
)
_EVENT_LATENCY = Histogram(
    "oraculo_event_latency_ms",
    "Latência ponta-a-ponta do evento",
    ["worker"],
    buckets=[50, 100, 250, 500, 1000, 2000, 5000],
)

# ── Stream Keys ───────────────────────────────────────────────────────────────
STREAM_RAG_REQUESTS = "oraculo:stream:rag_search_requests"
STREAM_STEP_RESULTS = "oraculo:stream:step_results"
CONSUMER_GROUP      = "oraculo-rag-workers"
STREAM_MAXLEN       = 5_000


@celery_app.task(
    name="worker_rag_search",
    bind=True,
    max_retries=2,
    queue="rag_search",
)
def worker_rag_search_task(self, event: dict) -> dict:
    """
    Task Celery que executa a busca RAG.
    Pode ser chamada diretamente OU via consumo do Stream.

    Args:
        event: dict com campos do evento (plan_id, session_id, step_id,
               doc_type, k, query, query_embedding_b64?)
    """
    t_start = time.monotonic()

    plan_id    = event.get("plan_id", "")
    session_id = event.get("session_id", "")
    step_id    = event.get("step_id", "s1")
    doc_type   = event.get("doc_type", "geral")
    k          = int(event.get("k", 6))
    query      = event.get("query", "")
    stream_id  = event.get("stream_id", "")

    logger.info("🔍 [RAG WORKER] Iniciando | plan=%s step=%s doc=%s",
                plan_id[:8], step_id, doc_type)

    try:
        result = _executar_busca(query=query, doc_type=doc_type, k=k)
        status = "ok"
        error  = ""
    except Exception as exc:
        logger.exception("❌ [RAG WORKER] Busca falhou: %s", exc)
        result = []
        status = "error"
        error  = str(exc)[:200]

    ms = int((time.monotonic() - t_start) * 1000)
    _RAG_LATENCY.observe(ms)
    _CHUNKS_RETURNED.observe(len(result))
    _EVENT_LATENCY.labels(worker="rag_search").observe(ms)

    payload = {
        "plan_id":    plan_id,
        "session_id": session_id,
        "step_id":    step_id,
        "worker":     "rag_search",
        "status":     status,
        "error":      error,
        "chunks":     result,
        "latency_ms": ms,
    }

    # Publica resultado no Stream de resultados
    _publicar_resultado(payload, stream_id)
    from src.infrastructure.redis_client import get_redis_text
    try:
        r = get_redis_text()
        cache_key = f"plan:results:{plan_id}:{step_id}"
        r.setex(cache_key, 120, json.dumps(
        {"chunks": result, "status": status},
        ensure_ascii=False
        ))

    except Exception as _e:
        logger.debug("Cache direto falhou (ignorado): %s", _e)
        
    

    logger.info("✅ [RAG WORKER] Concluído | %d chunks | %dms", len(result), ms)
    return payload


def _executar_busca(query: str, doc_type: str, k: int) -> list[dict]:
    """
    Usa busca híbrida via RedisVL (o adapter que já existe no projeto).
    Fallback para busca_hibrida síncrona se o adapter async não estiver disponível.
    """
    import asyncio
    import unicodedata

    def _normalizar(t: str) -> str:
        s = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode()
        return s.lower().strip()

    query_norm = _normalizar(query)

    # Gera embedding
    from src.rag.embeddings import get_embeddings
    emb_model = get_embeddings()
    vetor = emb_model.embed_query(query_norm)

    # source_filter por doc_type
    source_filter_map = {
        "calendario": None,
        "edital":     None,
        "contatos":   None,
        "wiki_ctic":  None,
        "geral":      None,
    }
    source_filter = source_filter_map.get(doc_type)

    from src.infrastructure.redis_client import busca_hibrida
    resultados = busca_hibrida(
        query_text=query_norm,
        query_embedding=vetor,
        source_filter=source_filter,
        k_vector=k,
        k_text=k + 2,
    )

    # Filtra por doc_type se não há source_filter específico
    if doc_type and doc_type != "geral":
        resultados = [r for r in resultados if r.get("doc_type") == doc_type] or resultados

    # Retorna apenas os campos necessários para o synthesis worker
    return [
        {
            "id":        r.get("id", ""),
            "content":   r.get("content", "")[:800],   # trunca para não explodir o Stream
            "source":    r.get("source", ""),
            "doc_type":  r.get("doc_type", ""),
            "rrf_score": r.get("rrf_score", 0.0),
        }
        for r in resultados[:k]
        if r.get("content", "").strip()
    ]


def _publicar_resultado(payload: dict, original_stream_id: str = "") -> None:
    """Publica o resultado no Redis Stream para o synthesis worker consumir."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()

        # Serializa chunks como JSON (campo único no Stream)
        stream_fields = {
            "plan_id":    payload["plan_id"],
            "session_id": payload["session_id"],
            "step_id":    payload["step_id"],
            "worker":     "rag_search",
            "status":     payload["status"],
            "error":      payload.get("error", ""),
            "result_json": json.dumps(
                {"chunks": payload["chunks"]}, ensure_ascii=False
            )[:8000],  # Redis Stream field limit
            "latency_ms": str(payload["latency_ms"]),
            "ts":         str(time.time()),
        }

        r.xadd(STREAM_STEP_RESULTS, stream_fields, maxlen=STREAM_MAXLEN, approximate=True)

        # XACK do evento original se veio de stream
        if original_stream_id:
            try:
                r.xack(STREAM_RAG_REQUESTS, CONSUMER_GROUP, original_stream_id)
            except Exception:
                pass

    except Exception as e:
        logger.error("❌ [RAG WORKER] Falha ao publicar resultado no Stream: %s", e)