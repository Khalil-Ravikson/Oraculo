# src/application/tasks/process_message_task.py
"""
Task Celery Principal — Processa mensagem com LangGraph + RedisSaver.

RESPONSABILIDADES:
  1. Adquire lock Redis (anti-processamento duplo)
  2. Dispara aviso de latência após 3s (asyncio)
  3. Invoca o grafo LangGraph com estado persistente (RedisSaver)
  4. Envia resposta final via Evolution API
  5. Libera lock

HITL (Human-in-the-Loop):
  Se o grafo parar em interrupt_before=["exec_tool_node"],
  a task termina sem resposta de tool.
  Na PRÓXIMA mensagem do usuário, o grafo retoma do estado salvo no Redis.
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.infrastructure.celery_app import celery_app
from src.infrastructure.redis_client import get_redis_text

logger = logging.getLogger(__name__)

_WARNING_DELAY = 3.0      # segundos antes de enviar "Aguarde..."
_WARNING_MSG   = "⏳ Processando, aguarde um instante..."


@celery_app.task(
    name        = "processar_mensagem",
    bind        = True,
    max_retries = 3,
    default_retry_delay = 5,
    queue       = "default",
)
def processar_mensagem_task(self, identity: dict) -> None:
    """
    Entry point da task Celery.
    Executa o loop assíncrono em thread dedicada.
    """
    asyncio.run(_processar_async(self, identity))


async def _processar_async(task, identity: dict) -> None:
    """Pipeline assíncrona completa."""
    phone   = identity.get("user_id") or identity.get("sender_phone", "unknown")
    chat_id = identity.get("chat_id", "")

    # ── 1. Lazy init (garante que índices e ingestão foram feitos) ────────────
    await asyncio.to_thread(_garantir_inicializado)

    # ── 2. Lock Redis ─────────────────────────────────────────────────────────
    r_text = get_redis_text()
    lock   = r_text.lock(f"lock:msg:{phone}", timeout=90, blocking_timeout=5)

    if not lock.acquire():
        logger.warning("🔒 Lock indisponível para %s — retry.", phone)
        raise task.retry(countdown=5)

    gateway = None
    warning_task = None

    try:
        # ── 3. Gateway de mensagens ───────────────────────────────────────────
        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
        gateway = EvolutionAdapter()

        # ── 4. Aviso de latência após 3s (não-bloqueante) ─────────────────────
        warning_task = asyncio.create_task(
            _aviso_latencia(gateway, chat_id, _WARNING_DELAY)
        )

        # ── 5. Verifica se Gemini está bloqueado pelo admin ───────────────────
        if r_text.get("admin:gemini_blocked") == "1":
            warning_task.cancel()
            await gateway.enviar_mensagem(
                chat_id,
                "🔧 Sistema temporariamente em manutenção. Tente novamente em breve.",
            )
            return

        # ── 6. Invoca o grafo LangGraph ───────────────────────────────────────
        from src.application.graph.builder import get_compiled_graph, get_graph_config
        from src.application.graph.state import OracleState

        graph  = get_compiled_graph()
        config = get_graph_config(thread_id=phone)
        state  = OracleState.from_identity(identity)

        t0     = time.monotonic()
        result = await graph.ainvoke(state, config=config)
        ms     = int((time.monotonic() - t0) * 1000)

        # ── 7. Cancela aviso de latência se ainda pendente ────────────────────
        if warning_task and not warning_task.done():
            warning_task.cancel()

        # ── 8. Envia resposta ─────────────────────────────────────────────────
        resposta = result.get("final_response")
        if resposta:
            await gateway.enviar_mensagem(chat_id, resposta)
            logger.info("✅ Resposta enviada | phone=%s | %dms", phone[-8:], ms)
        else:
            # Grafo pausou no interrupt_before — HITL em andamento
            # A próxima mensagem do usuário retomará de onde parou
            logger.info(
                "⏸️  Grafo pausado (HITL pendente) | phone=%s", phone[-8:]
            )

    except Exception as exc:
        if warning_task and not warning_task.done():
            warning_task.cancel()
        logger.exception("❌ Erro no processamento de %s: %s", phone, exc)

        if gateway:
            await gateway.enviar_mensagem(
                chat_id,
                "😕 Tive um problema técnico. Tente novamente em instantes.",
            )

        raise task.retry(exc=exc, countdown=5 ** (task.request.retries + 1))

    finally:
        try:
            lock.release()
        except Exception:
            pass


async def _aviso_latencia(gateway, chat_id: str, delay: float) -> None:
    """Envia aviso de 'aguarde' após N segundos se a resposta ainda não foi gerada."""
    await asyncio.sleep(delay)
    if chat_id:
        try:
            await gateway.enviar_mensagem(chat_id, _WARNING_MSG)
        except Exception:
            pass


def _garantir_inicializado() -> None:
    """Inicialização lazy — executado apenas na primeira task do worker."""
    from src.infrastructure.redis_client import inicializar_indices
    inicializar_indices()

    from src.application.graph.builder import get_compiled_graph
    get_compiled_graph()  # aquece o singleton do grafo