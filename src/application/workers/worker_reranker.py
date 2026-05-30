from __future__ import annotations
import asyncio, json, logging, time
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("reranker")
@celery_app.task(name="worker_reranker", bind=True, max_retries=1, queue="rag_search")
def worker_reranker_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {
      plan_id, session_id, step_id,
      depends_on: ["s1"],    # step do rag_search
      query: str,
      top_k: int = 5
    }
    Aguarda rag_search completar, então reordena.
    """
    plan_id   = event.get("plan_id", "")
    step_id   = event.get("step_id", "s_rerank")
    depends   = event.get("depends_on", [])
    query     = event.get("query", "")
    top_k     = int(event.get("top_k", 5))

    # Aguarda dependências (máx 10s)
    chunks = await _aguardar_chunks(plan_id, depends, timeout=10.0)

    if not chunks:
        payload = {"status": "error", "error": "sem chunks para reranker", "chunks": []}
        _salvar(plan_id, step_id, payload)
        return payload

    # Cross-encoder rerank (CPU, ~90MB)
    from src.application.chain.reranker import rerank
    t0 = time.monotonic()
    reranked = await rerank(query, chunks, top_k=top_k)
    ms = int((time.monotonic() - t0) * 1000)

    payload = {
        "status": "ok",
        "chunks": reranked,
        "reranked_count": len(reranked),
        "latency_ms": ms,
    }
    _salvar(plan_id, step_id, payload)
    logger.info("🏆 [RERANKER] %d→%d chunks | %dms | query='%.40s'",
                len(chunks), len(reranked), ms, query)
    return payload


async def _aguardar_chunks(plan_id: str, depends: list, timeout: float) -> list:
    """Polling nos resultados dos steps dependentes."""
    import time as _time
    from src.infrastructure.redis_client import get_redis_text

    if not depends:
        return []

    r        = get_redis_text()
    deadline = _time.monotonic() + timeout
    chunks   = []

    while _time.monotonic() < deadline:
        for dep in depends:
            key = f"plan:results:{plan_id}:{dep}"
            raw = r.get(key)
            if raw:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                chunks.extend(data.get("chunks", []))
        if chunks:
            return chunks
        await asyncio.sleep(0.2)
    return chunks


def _salvar(plan_id, step_id, data):
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 120,
            json.dumps(
                {k: v for k, v in data.items() if k != "chunks"},
                ensure_ascii=False
            )
        )
        # Salva chunks separado (pode ser grande)
        if data.get("chunks"):
            from src.infrastructure.redis_client import get_redis_text as _r
            _r().setex(
                f"plan:results:{plan_id}:{step_id}:chunks", 120,
                json.dumps(data["chunks"], ensure_ascii=False)
            )
    except Exception:
        pass