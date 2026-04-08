"""
application/tasks/process_message_task.py — Sprint 1 (Redis Streams + Langfuse)
================================================================================

MUDANÇAS vs versão anterior:
─────────────────────────────
  ADICIONADO (Sprint 1):
    - Aceita `stream_id` opcional → XACK quando processamento termina com sucesso
    - Recovery de XPENDING no startup do worker (_recover_pending_messages)
    - @observe_llm trace no nível da task (span de nível mais alto)
    - Langfuse flush no sinal SIGTERM

  MANTIDO:
    - Lock Redis por chat_id (anti-processamento duplo)
    - Aviso de latência após 3s (asyncio)
    - Grafo LangGraph com RedisSaver
    - Retry com backoff exponencial

FLUXO ATUALIZADO:
  Webhook → XADD(stream) + task.apply_async(stream_id=sid)
  Task    → executa grafo → envia resposta → XACK(stream_id)
  Restart → _recover_pending_messages() → requeue XPENDING

IMPORTANTE SOBRE O XACK:
  XACK só é chamado se a resposta foi enviada ao WhatsApp com sucesso.
  Em caso de erro → sem XACK → mensagem permanece em XPENDING.
  Após IDLE_MS_THRESHOLD (60s), outro worker reivindica via XAUTOCLAIM.
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.infrastructure.celery_app import celery_app
from src.infrastructure.redis_client import get_redis_text

logger = logging.getLogger(__name__)

_WARNING_DELAY = 3.0
_WARNING_MSG   = "⏳ Processando, aguarde um instante..."


# ─────────────────────────────────────────────────────────────────────────────
# Task principal
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name                = "processar_mensagem",
    bind                = True,
    max_retries         = 3,
    default_retry_delay = 5,
    queue               = "default",
)
def processar_mensagem_task(self, identity: dict, stream_id: str = "") -> None:
    """
    Entry point Celery.

    Args:
        identity:  dict com dados da mensagem (phone, body, chat_id, etc.)
        stream_id: ID do Redis Stream para XACK ao finalizar com sucesso.
                   Vazio string → mensagem não foi publicada no stream
                   (compatibilidade retroativa com código legado).
    """
    asyncio.run(_processar_async(self, identity, stream_id))


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline assíncrona principal
# ─────────────────────────────────────────────────────────────────────────────

async def _processar_async(task, identity: dict, stream_id: str) -> None:
    """
    Pipeline completa com Redis Streams acknowledgment e Langfuse tracing.
    """
    from src.infrastructure.observability.langfuse_client import langfuse_span

    phone   = identity.get("user_id") or identity.get("sender_phone", "unknown")
    chat_id = identity.get("chat_id", "")

    # ── 0. Langfuse span de nível superior ────────────────────────────────────
    with langfuse_span(
        name="processar_mensagem",
        input={
            "phone":     phone[-8:],
            "stream_id": stream_id,
            "body":      (identity.get("body") or "")[:100],
        },
        metadata={"queue": "default", "stream_id": stream_id},
    ) as span:

        # ── 1. Lazy init ──────────────────────────────────────────────────────
        await asyncio.to_thread(_garantir_inicializado)

        # ── 2. Lock Redis ─────────────────────────────────────────────────────
        r_text = get_redis_text()
        lock   = r_text.lock(f"lock:msg:{phone}", timeout=90, blocking_timeout=5)

        if not lock.acquire():
            logger.warning("🔒 Lock indisponível para %s — retry.", phone)
            # NÃO damos XACK: a mensagem vai para XPENDING e será recuperada
            raise task.retry(countdown=5)

        gateway = None
        warning_task = None
        success = False

        try:
            # ── 3. Gateway ────────────────────────────────────────────────────
            from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
            gateway = EvolutionAdapter()

            # ── 4. Aviso de latência ──────────────────────────────────────────
            warning_task = asyncio.create_task(
                _aviso_latencia(gateway, chat_id, _WARNING_DELAY)
            )

            # ── 5. Verifica bloqueio admin ────────────────────────────────────
            if r_text.get("admin:gemini_blocked") == "1":
                if warning_task and not warning_task.done():
                    warning_task.cancel()
                await gateway.enviar_mensagem(
                    chat_id,
                    "🔧 Sistema temporariamente em manutenção. Tente em breve.",
                )
                # XACK: mensagem tratada (resposta enviada = sucesso)
                success = True
                return

            # ── 6. Grafo LangGraph ────────────────────────────────────────────
            from src.application.graph.builder import get_compiled_graph, get_graph_config
            from src.application.graph.state import OracleState

            graph  = get_compiled_graph()
            config = get_graph_config(thread_id=phone)
            state  = OracleState.from_identity(identity)

            t0     = time.monotonic()
            result = await graph.ainvoke(state, config=config)
            ms     = int((time.monotonic() - t0) * 1000)

            # ── 7. Cancela aviso de latência ──────────────────────────────────
            if warning_task and not warning_task.done():
                warning_task.cancel()

            # ── 8. Envia resposta ─────────────────────────────────────────────
            resposta = result.get("final_response")
            if resposta:
                await gateway.enviar_mensagem(chat_id, resposta)
                success = True
                logger.info(
                    "✅ Resposta enviada | phone=%s | %dms | stream_id=%s",
                    phone[-8:], ms, stream_id or "n/a",
                )
            else:
                # Grafo pausou (HITL pendente) — considerar sucesso para o stream
                # O LangGraph persiste o estado no RedisSaver; retomada na próxima msg
                success = True
                logger.info("⏸️  Grafo pausado (HITL) | phone=%s", phone[-8:])

            # Atualiza o span Langfuse com métricas
            span.update(
                output={
                    "latencia_ms": ms,
                    "crag_score":  result.get("crag_score", 0.0),
                    "rota":        result.get("route", "?"),
                    "success":     success,
                }
            )

        except Exception as exc:
            if warning_task and not warning_task.done():
                warning_task.cancel()
            logger.exception("❌ Erro no processamento de %s: %s", phone, exc)

            if gateway:
                try:
                    await gateway.enviar_mensagem(
                        chat_id,
                        "😕 Tive um problema técnico. Tente novamente em instantes.",
                    )
                except Exception:
                    pass

            span.update(output={"error": str(exc)[:300]})
            # NÃO damos XACK em erro → mensagem fica em XPENDING para recovery
            raise task.retry(exc=exc, countdown=5 ** (task.request.retries + 1))

        finally:
            try:
                lock.release()
            except Exception:
                pass

        # ── 9. XACK — só após sucesso confirmado ──────────────────────────────
        # FORA do bloco try/except para garantir que o ACK só acontece
        # se o finally já limpou o lock (sem leaks)
        if success and stream_id:
            _xack_stream(stream_id)


def _xack_stream(stream_id: str) -> None:
    """
    Confirma o processamento no Redis Stream.
    Separado para isolar erros de ACK do fluxo principal.
    """
    try:
        from src.infrastructure.message_stream import get_message_stream
        get_message_stream().acknowledge(stream_id)
    except Exception as e:
        # Erro no XACK não quebra o fluxo — a mensagem vai para XPENDING
        # e um futuro worker tentará reprocessar (idempotência é necessária)
        logger.error("❌ XACK falhou para stream_id=%s: %s", stream_id, e)


async def _aviso_latencia(gateway, chat_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    if chat_id:
        try:
            await gateway.enviar_mensagem(chat_id, _WARNING_MSG)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Recovery de XPENDING no startup
# ─────────────────────────────────────────────────────────────────────────────

def recover_pending_messages() -> int:
    """
    Recupera mensagens XPENDING e as reenfileira no Celery.
    Chame no startup do worker (via signal `worker_ready`).

    Retorna o número de mensagens requeue-adas.
    """
    try:
        from src.infrastructure.message_stream import get_message_stream
        stream = get_message_stream()

        summary = stream.get_pending_summary()
        if summary.get("total", 0) == 0:
            logger.info("✅ Stream: nenhuma mensagem pendente no startup.")
            return 0

        logger.warning(
            "⚠️  Stream: %d mensagem(ns) pendente(s) detectada(s). Iniciando recovery...",
            summary["total"],
        )

        recovered = stream.recover_pending()
        n = 0
        for item in recovered:
            sid      = item["stream_id"]
            identity = item["identity"]
            # Reenfileira no Celery com o mesmo stream_id para XACK posterior
            processar_mensagem_task.apply_async(
                args=[identity, sid],
                queue="default",
            )
            n += 1
            logger.info(
                "🔄 Requeue: phone=%s stream_id=%s",
                identity.get("sender_phone", "?")[-8:], sid,
            )

        logger.info("✅ Stream Recovery: %d mensagem(ns) reenfileirada(s).", n)
        return n

    except Exception as e:
        logger.error("❌ Stream recovery falhou no startup: %s", e)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Inicialização lazy (mantida igual à versão anterior)
# ─────────────────────────────────────────────────────────────────────────────

def _garantir_inicializado() -> None:
    """Inicialização lazy — executado apenas na primeira task do worker."""
    from src.infrastructure.redis_client import inicializar_indices
    inicializar_indices()

    from src.application.graph.builder import get_compiled_graph
    get_compiled_graph()