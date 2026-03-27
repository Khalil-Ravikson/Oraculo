# src/application/tasks/process_message_task.py
from __future__ import annotations
import asyncio
import logging
import time

from langchain_core.messages import HumanMessage

from src.infrastructure.celery_app import celery_app
from src.infrastructure.redis_client import get_redis_text
from src.infrastructure.adapters.redis_cache_lock import RedisCacheLock

logger = logging.getLogger(__name__)

_WARNING_DELAY = 3.0  # segundos antes de enviar "Aguarde..."


@celery_app.task(
    name="processar_mensagem",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def processar_mensagem_task(self, identity: dict) -> None:
    phone   = identity["sender_phone"]
    chat_id = identity["chat_id"]

    asyncio.run(_processar(self, identity, phone, chat_id))


async def _processar(task, identity: dict, phone: str, chat_id: str) -> None:
    from src.application.graph.builder import get_compiled_graph
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter

    lock    = RedisCacheLock()
    gateway = EvolutionAdapter()

    # Adquire lock — o webhook só verifica, a task adquire
    if not await lock.acquire(phone, timeout=90):
        logger.warning("⚠️ Lock indisponível para %s — retry em 5s", phone)
        raise task.retry(countdown=5)

    try:
        # Aviso de latência (Regra 4)
        warning_task = asyncio.create_task(
            _aviso_latencia(gateway, chat_id, delay=_WARNING_DELAY)
        )

        graph  = get_compiled_graph()
        config = {"configurable": {"thread_id": phone}}

        state  = {
            "user_phone":   phone,
            "user_id":      identity["user_id"],
            "user_name":    identity["user_name"],
            "user_role":    identity["user_role"],
            "user_status":  identity["user_status"],
            "user_context": identity.get("user_context", {}),
            "current_input": identity["body"],
            "messages":     [HumanMessage(content=identity["body"])],
            # Preserva estado HITL se houver confirmação pendente
            "pending_confirmation": _get_pending(phone),
            "confirmation_result":  None,
        }

        result = await graph.ainvoke(state, config=config)

        warning_task.cancel()

        resposta = result.get("final_response")
        if resposta:
            await gateway.enviar_mensagem(chat_id, resposta)

        # Persiste confirmação pendente no Redis (entre turnos HITL)
        _set_pending(phone, result.get("pending_confirmation"))

    except Exception as exc:
        warning_task.cancel()
        logger.exception("❌ Erro no processamento de %s: %s", phone, exc)
        await gateway.enviar_mensagem(
            chat_id,
            "Desculpe, tive um problema técnico. Tente novamente. 🙏"
        )
        raise task.retry(exc=exc)

    finally:
        await lock.release(phone)


async def _aviso_latencia(gateway, chat_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    await gateway.enviar_mensagem(chat_id, "⏳ Processando, aguarde um instante...")


def _get_pending(phone: str) -> str | None:
    try:
        return get_redis_text().get(f"hitl:pending:{phone}")
    except Exception:
        return None


def _set_pending(phone: str, valor: str | None) -> None:
    r = get_redis_text()
    key = f"hitl:pending:{phone}"
    if valor:
        r.setex(key, 1800, valor)   # 30 min de TTL
    else:
        r.delete(key)