from __future__ import annotations
import asyncio, json, logging
from src.infrastructure.celery_app import celery_app
from src.application.workers.registry import register

logger = logging.getLogger(__name__)


@register("memory_manager")
@celery_app.task(name="worker_memory_manager", bind=True, max_retries=1, queue="default")
def worker_memory_manager_task(self, event: dict) -> dict:
    return asyncio.run(_run(event))


async def _run(event: dict) -> dict:
    """
    event: {
      plan_id, session_id, step_id,
      operation: "load" | "save" | "summarize" | "clear",
      user_id: str,
      turn: {role, content} (para save)
    }
    """
    operation  = event.get("operation", "load")
    session_id = event.get("session_id", "")
    user_id    = event.get("user_id", session_id)

    if operation == "load":
        payload = await _load(session_id, user_id)
    elif operation == "save":
        payload = await _save(session_id, event.get("turn", {}))
    elif operation == "summarize":
        payload = await _summarize(session_id)
    elif operation == "clear":
        payload = await _clear(session_id, user_id)
    else:
        payload = {"status": "error", "error": f"operation inválida: {operation}"}

    _salvar_resultado(event["plan_id"], event["step_id"], payload)
    return payload


async def _load(session_id: str, user_id: str) -> dict:
    try:
        from src.memory.container import create_memory_service
        svc     = create_memory_service()
        ctx     = svc.carregar_contexto(user_id, session_id)
        return {
            "status":   "ok",
            "historico": ctx.historico.texto_formatado,
            "fatos":    ctx.fatos_str,
            "sinais":   ctx.sinais,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)[:200], "historico": "", "fatos": ""}


async def _save(session_id: str, turn: dict) -> dict:
    if not turn:
        return {"status": "error", "error": "turn vazio"}
    try:
        from src.memory.adapters.redis_working_memory import RedisWorkingMemory
        from src.infrastructure.redis_client import get_redis_text
        mem = RedisWorkingMemory(get_redis_text())
        mem.add_turn(session_id, turn["role"], turn["content"])
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}


async def _summarize(session_id: str) -> dict:
    """Resume o histórico quando excede o budget de tokens."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.infrastructure.settings import settings
        import google.genai as genai
        from google.genai import types

        r   = get_redis_text()
        raw = r.lrange(f"chat:{session_id}", 0, -1)
        if len(raw) < 10:
            return {"status": "ok", "action": "noop", "reason": "historico pequeno"}

        turns = []
        for item in raw:
            d = json.loads(item)
            p = "Aluno" if d["role"] == "user" else "Bot"
            turns.append(f"{p}: {d['content'][:200]}")
        conversa = "\n".join(turns)

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        resp   = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Resuma esta conversa em 3-5 bullet points preservando fatos importantes:\n{conversa}",
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=200),
        )
        summary = (resp.text or "").strip()

        # Substitui histórico pelo resumo
        summary_entry = json.dumps(
            {"role": "assistant", "content": f"[RESUMO ANTERIOR]\n{summary}"},
            ensure_ascii=False
        )
        r.delete(f"chat:{session_id}")
        r.rpush(f"chat:{session_id}", summary_entry)
        r.expire(f"chat:{session_id}", 1800)

        return {"status": "ok", "action": "summarized", "summary": summary}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}


async def _clear(session_id: str, user_id: str) -> dict:
    try:
        from src.memory.container import create_memory_service
        create_memory_service().limpar_tudo(user_id, session_id)
        return {"status": "ok", "action": "cleared"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}


def _salvar_resultado(plan_id, step_id, data):
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().setex(
            f"plan:results:{plan_id}:{step_id}", 120,
            json.dumps(data, ensure_ascii=False)
        )
    except Exception:
        pass