"""
application/tasks/process_message_task.py — v5 (LangChain Runnables)
=====================================================================

MUDANÇA PRINCIPAL:
  ANTES: graph.invoke(state, config) — LangGraph opaco, logs silenciosos
  DEPOIS: chain.invoke(message, session_id, ctx) — pipeline linear, debug completo

O resto do fluxo é idêntico:
  - Redis Lock (anti-spam)
  - Mensagem de aviso após 3s
  - XACK no Redis Stream
  - Recovery de XPENDING no startup
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


@celery_app.task(
    name="processar_mensagem",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    queue="default",
)
def processar_mensagem_task(self, identity: dict, stream_id: str = "") -> None:
    """Entry point Celery. Usa OracleChain (LangChain) para gerar resposta."""
    asyncio.run(_processar_async(self, identity, stream_id))


async def _processar_async(task, identity: dict, stream_id: str) -> None:
    phone   = identity.get("user_id") or identity.get("sender_phone", "unknown")
    chat_id = identity.get("chat_id", "")
    message = identity.get("body", "")




    # ── Validação de identidade (Porteiro) ────────────────────────────────────
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.pessoa_repository import PessoaRepository

    async with AsyncSessionLocal() as db:
        repo = PessoaRepository(db)
        identidade = await repo.obter_identidade_por_telefone(phone, chat_id)

    if not identidade:
        logger.info("🚫 [TASK] Usuário não cadastrado: %s", phone[-6:])
        await gateway.enviar_mensagem(
            chat_id,
            "👋 Para usar o Oráculo, você precisa estar cadastrado. "
            "Entre em contato com a secretaria ou CTIC."
        )
        success = True
        return

    if identidade.status != "ativo":
        logger.info("🚫 [TASK] Usuário inativo: %s | status=%s", phone[-6:], identidade.status)
        success = True
        return

    # Monta user_context rico a partir da identidade
    user_context = identidade.contexto_llm
    user_context["role"] = identidade.role
    user_context["is_admin"] = identidade.is_admin

    if not message.strip():
        logger.debug("⏭️  Mensagem vazia ignorada para %s", phone)
        return

    # ── Lock Redis ─────────────────────────────────────────────────────────────
    r_text  = get_redis_text()
    lock    = r_text.lock(f"lock:msg:{phone}", timeout=90, blocking_timeout=5)

    if not lock.acquire():
        logger.warning("🔒 Lock indisponível para %s — retry.", phone[-6:])
        raise task.retry(countdown=5)

    gateway      = None
    warning_task = None
    success      = False

    try:
        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
        gateway = EvolutionAdapter()

        # ── Aviso de latência após 3s ──────────────────────────────────────────
        warning_task = asyncio.create_task(
            _aviso_latencia(gateway, chat_id, _WARNING_DELAY)
        )

        # ── Verifica bloqueio admin ────────────────────────────────────────────
        if r_text.get("admin:maintenance_mode") == "1":
            if not identity.get("is_admin"):
                if warning_task and not warning_task.done():
                    warning_task.cancel()
                await gateway.enviar_mensagem(
                    chat_id, "🔧 Sistema em manutenção. Volte em breve!"
                )
                success = True
                return

        # ── Monta contexto do usuário ──────────────────────────────────────────
        user_context = {
            "nome":      identity.get("nome", ""),
            "curso":     identity.get("curso", ""),
            "periodo":   identity.get("periodo", ""),
            "matricula": identity.get("matricula", ""),
            "centro":    identity.get("centro", ""),
            "role":      identity.get("role", "estudante"),
        }

        # ── Executa a chain ────────────────────────────────────────────────────
        from src.application.chain.oracle_chain import get_oracle_chain
        chain = get_oracle_chain()

        t0 = time.monotonic()
        result = await chain.invoke(
            message=message,
            session_id=phone,
            user_context=user_context,
        )
        ms = int((time.monotonic() - t0) * 1000)

        # ── Cancela aviso de latência ──────────────────────────────────────────
        if warning_task and not warning_task.done():
            warning_task.cancel()

        # ── Envia resposta ─────────────────────────────────────────────────────
        if result.answer:
            await gateway.enviar_mensagem(chat_id, result.answer)
            success = True
            logger.info(
                "✅ [TASK] Resposta enviada | phone=%s | %dms | route=%s | "
                "crag=%.3f | tokens=%d",
                phone[-6:], ms, result.route, result.crag_score, result.tokens_used,
            )
        else:
            logger.warning("⚠️  [TASK] Chain retornou resposta vazia para %s", phone[-6:])
            success = True   # não falha — pode ser HITL pendente

        # ── Registra métricas no Redis ─────────────────────────────────────────
        _salvar_metrica(phone, result)

    except Exception as exc:
        if warning_task and not warning_task.done():
            warning_task.cancel()
        logger.exception("❌ [TASK] Erro fatal para %s: %s", phone[-6:], exc)
        if gateway:
            try:
                await gateway.enviar_mensagem(
                    chat_id,
                    "😕 Tive um problema técnico. Tente novamente em instantes."
                )
            except Exception:
                pass
        raise task.retry(exc=exc, countdown=5 ** (task.request.retries + 1))

    finally:
        try:
            lock.release()
        except Exception:
            pass

    if success and stream_id:
        _xack_stream(stream_id)


async def _aviso_latencia(gateway, chat_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    if chat_id:
        try:
            await gateway.enviar_mensagem(chat_id, _WARNING_MSG)
        except Exception:
            pass


def _xack_stream(stream_id: str) -> None:
    try:
        from src.infrastructure.message_stream import get_message_stream
        get_message_stream().acknowledge(stream_id)
    except Exception as e:
        logger.error("❌ XACK falhou para %s: %s", stream_id, e)


def _salvar_metrica(phone: str, result) -> None:
    """Persiste métricas no Redis para o monitor."""
    try:
        import json
        from datetime import datetime
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        entrada = json.dumps({
            "ts":         datetime.now().isoformat(),
            "user_id":    phone[-8:],
            "route":      result.route,
            "crag_score": result.crag_score,
            "tokens":     result.tokens_used,
            "total_ms":   result.total_ms,
            "chunks":     result.chunks_count,
        }, ensure_ascii=False)
        r.lpush("monitor:logs", entrada)
        r.ltrim("monitor:logs", 0, 499)
    except Exception:
        pass


def recover_pending_messages() -> int:
    """Recovery de XPENDING no startup do worker."""
    try:
        from src.infrastructure.message_stream import get_message_stream
        stream = get_message_stream()
        summary = stream.get_pending_summary()
        if summary.get("total", 0) == 0:
            logger.info("✅ [TASK] Sem mensagens pendentes no startup.")
            return 0
        logger.warning("⚠️  [TASK] %d mensagem(ns) pendente(s). Iniciando recovery...",
                       summary["total"])
        recovered = stream.recover_pending()
        n = 0
        for item in recovered:
            sid      = item["stream_id"]
            identity = item["identity"]
            processar_mensagem_task.apply_async(args=[identity, sid], queue="default")
            n += 1
        logger.info("✅ [TASK] %d mensagem(ns) recuperada(s).", n)
        return n
    except Exception as e:
        logger.error("❌ [TASK] Stream recovery falhou: %s", e)
        return 0