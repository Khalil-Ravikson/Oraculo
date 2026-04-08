"""
infrastructure/celery_app.py — Sprint 1 (Recovery Signal + Stream Health)
=========================================================================

MUDANÇAS vs versão anterior:
  ADICIONADO:
    - Signal worker_ready → chama recover_pending_messages() no startup
    - Signal worker_shutdown → flush Langfuse antes de encerrar
    - Fila "streams" para o recovery task periódico (opcional)

  MANTIDO:
    - Beat schedule (notificações diárias)
    - Task routing (default / notificacoes / admin)
    - Timezone America/Sao_Paulo
"""
import os
import logging
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready, worker_shutdown

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0").replace("/0", "/2")

celery_app = Celery(
    "bot_tasks",
    broker  = REDIS_URL,
    backend = REDIS_URL,
)

celery_app.conf.update(
    # ── Serialização ──────────────────────────────────────────────────────────
    task_serializer   = "json",
    accept_content    = ["json"],
    result_serializer = "json",

    # ── Timezone ──────────────────────────────────────────────────────────────
    timezone   = "America/Sao_Paulo",
    enable_utc = True,

    # ── Confiabilidade ────────────────────────────────────────────────────────
    task_acks_late             = True,
    worker_prefetch_multiplier = 1,

    # ── Tasks ─────────────────────────────────────────────────────────────────
    include = [
        "src.application.tasks",
        "src.application.tasks_notificacao",
        "src.application.tasks_admin",
    ],

    # ── Beat Schedule ─────────────────────────────────────────────────────────
    beat_schedule = {
        "verificar_prazos_dias_uteis": {
            "task":     "verificar_e_notificar_prazos",
            "schedule": crontab(hour=8, minute=0, day_of_week="1-5"),
            "options":  {"queue": "notificacoes"},
        },
        "verificar_prazos_fim_semana": {
            "task":     "verificar_e_notificar_prazos",
            "schedule": crontab(hour=9, minute=0, day_of_week="0,6"),
            "options":  {"queue": "notificacoes"},
        },
        # ── Sprint 1: recovery periódico (opcional, defensivo) ────────────────
        # Verifica XPENDING a cada 5 minutos — garante zero perda mesmo se o
        # signal worker_ready falhar por algum motivo no startup.
        "stream_recovery_periodico": {
            "task":     "stream_recovery",
            "schedule": crontab(minute="*/5"),
            "options":  {"queue": "default", "expires": 240},
        },
    },

    # ── Routing ───────────────────────────────────────────────────────────────
    task_default_queue = "default",
    task_routes = {
        "processar_mensagem":           {"queue": "default"},
        "verificar_e_notificar_prazos": {"queue": "notificacoes"},
        "notificar_evento_especifico":  {"queue": "notificacoes"},
        "ingerir_documento_whatsapp":   {"queue": "admin"},
        "executar_comando_admin":       {"queue": "admin"},
        "stream_recovery":              {"queue": "default"},
    },

    beat_scheduler          = "celery.beat:PersistentScheduler",
    beat_schedule_filename  = "/tmp/celery_beat_schedule",
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Signal: worker_ready — Recovery de XPENDING no startup
# ─────────────────────────────────────────────────────────────────────────────

@worker_ready.connect
def on_worker_ready(sender=None, **kwargs):
    """
    Executado quando o worker está pronto para receber tasks.

    Sprint 1: recupera mensagens XPENDING do Redis Stream.
    Se o container caiu no meio de um processamento, essas mensagens
    ficaram sem XACK e serão reenfileiradas aqui.
    """
    tasks = [t for t in celery_app.tasks.keys() if not t.startswith("celery.")]
    logger.info("✅ [CELERY] Worker pronto. Tasks (%d): %s", len(tasks), tasks)

    # ── Recovery de XPENDING ──────────────────────────────────────────────────
    try:
        from src.application.tasks.process_message_task import recover_pending_messages
        n = recover_pending_messages()
        if n > 0:
            logger.warning("🔄 [CELERY] %d mensagem(ns) recuperada(s) do Stream.", n)
        else:
            logger.info("✅ [CELERY] Stream sem mensagens pendentes.")
    except Exception as e:
        logger.error("❌ [CELERY] Stream recovery falhou no startup: %s", e)

    # ── Log do Beat schedule ───────────────────────────────────────────────────
    schedules = list(celery_app.conf.beat_schedule.keys())
    logger.info("📅 Beat schedules: %s", schedules)


# ─────────────────────────────────────────────────────────────────────────────
# Signal: worker_shutdown — Flush Langfuse antes de encerrar
# ─────────────────────────────────────────────────────────────────────────────

@worker_shutdown.connect
def on_worker_shutdown(sender=None, **kwargs):
    """
    Garante que todos os spans Langfuse pendentes sejam enviados antes
    do worker encerrar (SIGTERM / docker stop).
    """
    try:
        from src.infrastructure.observability.langfuse_client import flush_langfuse
        flush_langfuse()
        logger.info("✅ [CELERY] Langfuse spans flushed no shutdown.")
    except Exception as e:
        logger.debug("ℹ️  [CELERY] Langfuse flush ignorado: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Task: stream_recovery (periódica, defensiva)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="stream_recovery", bind=True)
def stream_recovery_task(self) -> dict:
    """
    Task periódica que verifica e requeue mensagens XPENDING.
    Executada a cada 5 minutos pelo Celery Beat.
    É uma camada de segurança extra além do signal worker_ready.
    """
    try:
        from src.application.tasks.process_message_task import recover_pending_messages
        n = recover_pending_messages()
        return {"recovered": n, "status": "ok"}
    except Exception as e:
        logger.error("❌ stream_recovery_task: %s", e)
        return {"recovered": 0, "status": "error", "error": str(e)}