from __future__ import annotations
import asyncio, logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("graph_extractor")
@celery_app.task(name="worker_graph_extractor", bind=True, max_retries=2, queue="graph")
def worker_graph_extractor_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {plan_id, session_id, step_id,
            text: str, source: str, doc_type: str}
    """
    from src.infrastructure.services.graph_extractor_service import GraphExtractorService

    svc    = GraphExtractorService()
    result = await svc.extract_and_save(
        text=event.get("text", ""),
        source=event.get("source", "unknown"),
        doc_type=event.get("doc_type", "geral"),
    )

    payload = {
        "status":          "ok" if result.ok else "error",
        "entities_saved":  result.entities_saved,
        "triples_saved":   result.triples_saved,
        "entities":        result.entities,
        "error":           result.error,
    }
    _salvar(event["plan_id"], event["step_id"], payload)
    logger.info("🕸️  [GRAPH] %d entidades | %d triplas | source=%s",
                result.entities_saved, result.triples_saved, event.get("source","?"))
    return payload


def _salvar(plan_id, step_id, data):
    import json
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 120,
            json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        pass