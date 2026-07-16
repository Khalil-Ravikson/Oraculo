from __future__ import annotations
import asyncio, json, logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("db_connector")
@celery_app.task(name="worker_db_connector", bind=True, max_retries=2, queue="default")
def worker_db_connector_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {
      plan_id, session_id, step_id,
      query_type: "dados_aluno" | "notas",
      matricula: str,
      semestre: str (opcional)
    }
    """
    from src.infrastructure.services.db_connector_service import DBConnectorService

    svc        = DBConnectorService()
    qtype      = event.get("query_type", "dados_aluno")
    matricula  = event.get("matricula", "")

    if not matricula:
        payload = {"status": "error", "error": "matricula obrigatória", "context": ""}
        _salvar(event["plan_id"], event["step_id"], payload)
        return payload

    if qtype == "notas":
        result = await svc.buscar_notas(matricula, event.get("semestre", ""))
    else:
        result = await svc.buscar_dados_aluno(matricula)

    payload = {
        "status":  "ok" if result.ok else "error",
        "context": result.to_context_str(),
        "data":    result.data,
        "error":   result.error,
    }
    _salvar(event["plan_id"], event["step_id"], payload)
    logger.info("🗄️  [DBCONN] %s | matricula=%s | ok=%s",
                qtype, matricula[-4:], result.ok)
    return payload


def _salvar(plan_id, step_id, data):
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 120,
            json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        pass