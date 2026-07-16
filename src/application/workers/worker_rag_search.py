"""
src/application/workers/worker_rag_search.py  (REFATORADO)
-----------------------------------------------------------
Worker puro: instancia RAGSearchService, invoca, publica resultado.
Toda lógica de busca está em src/infrastructure/services/rag_search_service.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from prometheus_client import Counter, Histogram

from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register 

logger = logging.getLogger(__name__)

_RAG_LATENCY = Histogram(
    "oraculo_rag_search_latency_ms", "Latência RAG em ms",
    buckets=[10, 25, 50, 100, 250, 500, 1000],
)
_CHUNKS_HIST = Histogram(
    "oraculo_rag_search_chunks_returned", "Chunks retornados",
    buckets=[0, 1, 3, 5, 8, 10, 15],
)

STREAM_STEP_RESULTS  = "oraculo:stream:step_results"
RESULTS_CACHE_PREFIX = "plan:results:"
RESULTS_TTL          = 120
STREAM_MAXLEN        = 5_000


@register("rag_search") # NOVO DECORADOR
@celery_app.task(name="worker_rag_search", bind=True, max_retries=3)
def worker_rag_search_task(self, event: dict) -> dict:
    return asyncio.run(_executar(self, event))


async def _executar(task, event: dict) -> dict:
    t0 = time.monotonic()

    plan_id    = event.get("plan_id", "")
    session_id = event.get("session_id", "")
    step_id    = event.get("step_id", "s1")
    doc_type   = event.get("doc_type", "geral")
    k_vector   = int(event.get("k_vector", 6))
    k_text     = int(event.get("k_text", 8))
    query      = event.get("query", "")
    rota       = event.get("rota", "GERAL")
    plan_ctx   = event.get("plan_context", {})
    fatos      = event.get("fatos") or plan_ctx.get("fatos", [])
    historico  = event.get("historico") or plan_ctx.get("history", "")

    logger.info("🔍 [RAG WORKER] plan=%s step=%s doc=%s", plan_id[:8], step_id, doc_type)

    metadata_filter = {"ano": "2026"}
    if doc_type and doc_type != "geral":
        metadata_filter["tipo_doc"] = doc_type.capitalize()

    try:
        from src.agents.academic_knowledge.service import RAGSearchService
        svc = RAGSearchService()
        result = await svc.buscar(
            query=query,
            doc_type=doc_type,
            k_vector=k_vector,
            k_text=k_text,
            rota=rota,
            fatos=fatos,
            historico=historico,
            metadata_filter=metadata_filter,
        )
        chunks = result.data.get("chunks", [])
        status = "ok" if result.ok else "error"
        error  = result.error

    except Exception as exc:
        logger.exception("❌ [RAG WORKER] falhou: %s", exc)
        chunks = []
        status = "error"
        error  = str(exc)[:200]
        raise task.retry(exc=exc)

    ms = int((time.monotonic() - t0) * 1000)
    _RAG_LATENCY.observe(ms)
    _CHUNKS_HIST.observe(len(chunks))

    payload = {
        "plan_id":    plan_id,
        "session_id": session_id,
        "step_id":    step_id,
        "worker":     "rag_search",
        "status":     status,
        "error":      error,
        "chunks":     [
            {k: v for k, v in c.items() if k != "embedding"}  # não serializar vetores
            for c in chunks
        ],
        "latency_ms": ms,
    }

    _publicar(payload)
    logger.info("✅ [RAG WORKER] %d chunks | %dms", len(chunks), ms)
    return payload


def _publicar(payload: dict) -> None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        plan_id = payload["plan_id"]
        step_id = payload["step_id"]

        # Cache direto para o synthesis worker consumir via polling
        r.setex(
            f"{RESULTS_CACHE_PREFIX}{plan_id}:{step_id}",
            RESULTS_TTL,
            json.dumps({"chunks": payload["chunks"], "status": payload["status"]},
                       ensure_ascii=False),
        )
        # Stream para observabilidade
        r.xadd(
            STREAM_STEP_RESULTS,
            {
                "plan_id":     payload["plan_id"],
                "session_id":  payload["session_id"],
                "step_id":     step_id,
                "worker":      "rag_search",
                "status":      payload["status"],
                "result_json": json.dumps(
                    {"chunks": payload["chunks"]}, ensure_ascii=False
                )[:6000],
                "latency_ms":  str(payload["latency_ms"]),
            },
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    except Exception as e:
        logger.warning("⚠️  [RAG WORKER] publicar falhou: %s", e)