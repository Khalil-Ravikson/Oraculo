"""
infrastructure/message_stream.py — Redis Streams Journal (Sprint 1)
====================================================================

PROBLEMA RESOLVIDO:
  Celery + Redis usa LPUSH/BRPOP sobre Listas.
  Se o worker cair durante o processamento, a mensagem desaparece.

  Redis Streams (XADD / XREADGROUP / XACK) corrigem isso:
    XADD        → grava no log imutável e persistente (sobrevive restarts)
    XREADGROUP  → worker "reserva" a mensagem (não some da fila)
    XACK        → confirma processamento bem-sucedido
    XAUTOCLAIM  → recovery: reivindica pendentes de workers mortos

FLUXO COMPLETO:
  ┌─ Webhook ──────────────────────────────────────────────────┐
  │  stream_id = stream.publish(identity)   ← XADD             │
  │  task.apply_async(args=[identity, stream_id])               │
  └────────────────────────────────────────────────────────────┘
              ↓
  ┌─ Celery Worker ────────────────────────────────────────────┐
  │  processar_mensagem_task(identity, stream_id)               │
  │    → processa mensagem                                      │
  │    → stream.acknowledge(stream_id)      ← XACK             │
  └────────────────────────────────────────────────────────────┘

RECOVERY (startup do worker):
  _recover_pending() → XPENDING_RANGE → mensagens sem XACK
  há > IDLE_MS_THRESHOLD → XAUTOCLAIM → Celery requeue

LIMITES:
  maxlen=10_000 → trimming automático (log circular)
  Para auditoria long-term use o monitor:logs do Redis existente.
"""
from __future__ import annotations

import json
import logging
import os
import time
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

STREAM_KEY          = "oraculo:stream:messages"
CONSUMER_GROUP      = "oraculo-workers"
STREAM_MAXLEN       = 10_000        # manter as 10k mais recentes
IDLE_MS_THRESHOLD   = 60_000        # 60s sem ACK → considerar morto
RECOVERY_BATCH      = 50            # máximo de pendentes por recovery cycle


def _consumer_name() -> str:
    """Nome único por processo (garante consumer groups corretos)."""
    return f"worker-{os.getpid()}"


# ─────────────────────────────────────────────────────────────────────────────
# MessageStream
# ─────────────────────────────────────────────────────────────────────────────

class MessageStream:
    """
    Journal imutável de mensagens via Redis Streams.

    THREAD SAFETY: `redis-py` é thread-safe por design (connection pool).
    PROCESS SAFETY: consumer_name inclui o PID, evitando colisão entre workers.
    """

    def __init__(self):
        # Usa o cliente de texto existente (decode_responses=True)
        from src.infrastructure.redis_client import get_redis_text
        self._r = get_redis_text()
        self._ensure_group()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _ensure_group(self) -> None:
        """Cria o consumer group se não existir (idempotente)."""
        try:
            # id="0" → novo grupo lê DESDE O INÍCIO do stream
            # mkstream=True → cria o stream se não existir
            self._r.xgroup_create(
                STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True
            )
            logger.info("✅ Stream consumer group '%s' criado.", CONSUMER_GROUP)
        except Exception:
            # group já existe — normal em restarts
            pass

    # ── Publisher ─────────────────────────────────────────────────────────────

    def publish(self, identity: dict) -> str:
        """
        Grava a mensagem no stream imutável.
        Retorna o stream_id (ex: "1718000000000-0") para usar no XACK.

        O stream_id é passado para o Celery task e usado na confirmação.
        Sem XACK, a mensagem permanece em XPENDING e é recuperada no próximo restart.
        """
        payload = {
            "identity": json.dumps(identity, ensure_ascii=False),
            "ts":       str(time.time()),
            "pid":      str(os.getpid()),
        }
        stream_id = self._r.xadd(
            STREAM_KEY,
            payload,
            maxlen=STREAM_MAXLEN,
            approximate=True,   # MAXLEN ~ N (mais eficiente)
        )
        # redis-py retorna bytes: decodifica para str
        sid = stream_id.decode() if isinstance(stream_id, bytes) else stream_id
        logger.debug("📝 Stream XADD: %s | phone=%s", sid, identity.get("sender_phone", "?")[-8:])
        return sid

    # ── Acknowledgment ────────────────────────────────────────────────────────

    def acknowledge(self, stream_id: str) -> None:
        """
        Confirma que a mensagem foi processada com sucesso.
        Remove de XPENDING — o worker não vai reprocessar.

        Deve ser chamado APENAS após envio bem-sucedido da resposta ao WhatsApp.
        Em caso de erro, NÃO chamar XACK → mensagem fica em XPENDING para retry.
        """
        if not stream_id:
            return
        try:
            n = self._r.xack(STREAM_KEY, CONSUMER_GROUP, stream_id)
            logger.debug("✅ Stream XACK: %s (n=%d)", stream_id, n)
        except Exception as e:
            logger.warning("⚠️  Stream XACK falhou [%s]: %s", stream_id, e)

    # ── Recovery ──────────────────────────────────────────────────────────────

    def get_pending_summary(self) -> dict:
        """
        Resumo dos pendentes — útil para health check e logs de startup.
        """
        try:
            info = self._r.xpending(STREAM_KEY, CONSUMER_GROUP)
            return {
                "total":    info.get("pending", 0),
                "min_id":   info.get("min", ""),
                "max_id":   info.get("max", ""),
                "consumers": info.get("consumers", []),
            }
        except Exception:
            return {"total": 0}

    def recover_pending(self) -> list[dict]:
        """
        Reivindica mensagens que estão em XPENDING há mais de IDLE_MS_THRESHOLD.
        Retorna lista de identities para requeue no Celery.

        Quando usar:
          - Startup do worker (garante zero perda após restart)
          - Tarefa periódica (Celery Beat a cada 5 min, opcional)
        """
        consumer = _consumer_name()
        recovered: list[dict] = []

        try:
            # XPENDING_RANGE: lista mensagens pendentes
            pending = self._r.xpending_range(
                STREAM_KEY, CONSUMER_GROUP,
                min="-", max="+",
                count=RECOVERY_BATCH,
            )
            if not pending:
                return []

            logger.info("⚠️  Stream: %d mensagem(ns) pendente(s) detectada(s).", len(pending))

            for entry in pending:
                idle_ms = entry.get("time_since_delivered", 0)
                if idle_ms < IDLE_MS_THRESHOLD:
                    continue  # worker ainda pode estar processando

                msg_id = entry["message_id"]
                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id

                # XAUTOCLAIM: toma posse da mensagem e lê seu conteúdo
                try:
                    # Sintaxe: XAUTOCLAIM key group consumer min-idle-time start count
                    result = self._r.xautoclaim(
                        STREAM_KEY, CONSUMER_GROUP, consumer,
                        IDLE_MS_THRESHOLD,
                        start_id=msg_id_str,
                        count=1,
                    )
                    # result = [next_start_id, [[id, fields], ...], [deleted_ids]]
                    claimed_messages = result[1] if result and len(result) > 1 else []

                    for claimed_id, fields in claimed_messages:
                        raw = fields.get("identity") or fields.get(b"identity")
                        if raw:
                            identity = json.loads(
                                raw.decode() if isinstance(raw, bytes) else raw
                            )
                            recovered.append({
                                "stream_id": claimed_id.decode() if isinstance(claimed_id, bytes) else claimed_id,
                                "identity":  identity,
                            })
                            logger.warning(
                                "🔄 Stream Recovery: requeue phone=%s stream_id=%s",
                                identity.get("sender_phone", "?")[-8:], claimed_id,
                            )
                except Exception as e:
                    logger.error("❌ XAUTOCLAIM falhou para %s: %s", msg_id_str, e)

        except Exception as e:
            logger.error("❌ Stream recovery falhou: %s", e)

        return recovered

    # ── Diagnóstico ───────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Informações do stream para o health check / admin portal."""
        try:
            info = self._r.xinfo_stream(STREAM_KEY)
            return {
                "length":         info.get("length", 0),
                "first_entry_id": str(info.get("first-entry", [""])[0]),
                "last_entry_id":  str(info.get("last-entry", [""])[0]),
                "consumer_group": CONSUMER_GROUP,
                "pending":        self.get_pending_summary(),
            }
        except Exception as e:
            return {"erro": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_message_stream() -> MessageStream:
    """
    Singleton do MessageStream.
    lru_cache garante uma única instância (e um único connection pool).
    """
    return MessageStream()