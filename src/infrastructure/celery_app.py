"""
infrastructure/celery_app.py — configuração do Celery
====================================================

- Broker Redis DB/1, result backend DB/2 (o app fica na DB/0).
- Filas reais em `task_routes`: default / admin / rag_search / synthesis /
  media / graph. Timezone America/Sao_Paulo.
- `worker_ready` recupera mensagens XPENDING do Redis Stream no boot.
- `worker_process_init`/`shutdown` mantêm um event loop asyncio persistente
  por processo (necessário pro AsyncRedisSaver do grafo — ver abaixo).
"""
import asyncio
import os
import logging
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready
from src.infrastructure.settings import settings as _settings
celery_broker_url = _settings.REDIS_URL.replace("/0", "/1")
celery_backend_url = _settings.REDIS_URL.replace("/0", "/2")



celery_app = Celery(
    "bot_tasks",
    broker  = celery_broker_url,
    backend = celery_backend_url,
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
        'src.application.tasks.ingestion_tasks', # <--- ADICIONE ESTA LINHA!
        "src.application.tasks.tasks_admin",
        "src.application.workers.worker_rag_search",    # ← NOVO
        "src.application.workers.worker_synthesis",     # ← NOVO
        "src.application.tasks.beat_nightly_memory",    # ← NOVO
        "src.application.workers.worker_audio_to_text",
        "src.application.workers.worker_text_to_audio",
        "src.application.workers.worker_media_download",
        "src.application.workers.worker_graph_extractor",
        "src.application.workers.worker_db_connector",
        "src.application.workers.worker_memory_manager",
        "src.application.workers.worker_reranker",
        "src.application.workers.worker_sigaa",
    ],

    # ── Beat Schedule ─────────────────────────────────────────────────────────
    beat_schedule = {
        "nightly_memory_sync": {
            "task":     "beat_nightly_memory_sync",
            "schedule": crontab(hour=2, minute=0),
            "options":  {"queue": "default"},
        },

        # ── Sprint 1: recovery periódico (opcional, defensivo) ────────────────
        # Verifica XPENDING a cada 5 minutos — garante zero perda mesmo se o
        # signal worker_ready falhar por algum motivo no startup.
        "stream_recovery_periodico": {
            "task":     "stream_recovery",
            "schedule": crontab(minute="*/5"),
            "options":  {"queue": "default", "expires": 240},
        },
        "sigaa_monitorar_processos": {
            "task":     "worker_sigaa_processos",
            "schedule": crontab(hour=8, minute=0),
            "args":     [{"plan_id": "beat", "session_id": "beat", "nivel": "L"}],
            "options":  {"queue": "default"},
        },
        # Taxa USD→BRL ao vivo (custo real em dinheiro no /hub/llm-custo) —
        # a cada 6h é sobra de margem pra câmbio (não é day-trading), evita
        # martelar a API gratuita. Só ATUALIZA o valor auto; override manual
        # via /hub/llm-custo continua tendo precedência (ver pricing.py::taxa_brl_ativa).
        "atualizar_taxa_brl": {
            "task":     "atualizar_taxa_brl",
            "schedule": crontab(minute=0, hour="*/6"),
            "options":  {"queue": "default"},
        },
    },

    # ── Routing ───────────────────────────────────────────────────────────────
    task_default_queue = "default",
    task_routes = {
        "processar_mensagem":           {"queue": "default"},

        "ingerir_documento_whatsapp":   {"queue": "admin"},
        "executar_comando_admin":       {"queue": "admin"},
        "stream_recovery":              {"queue": "default"},
        "worker_rag_search":          {"queue": "rag_search"},
        "worker_synthesis":           {"queue": "synthesis"},
        "beat_nightly_memory_sync":   {"queue": "default"},
        "atualizar_taxa_brl":         {"queue": "default"},
        "worker_audio_to_text":   {"queue": "media"},
        "worker_text_to_audio":   {"queue": "media"},
        "worker_ytb_download":    {"queue": "media"},
        "worker_insta_download":  {"queue": "media"},
        "worker_graph_extractor": {"queue": "graph"},
        "worker_db_connector":    {"queue": "default"},
        "worker_memory_manager":  {"queue": "default"},
        "worker_reranker":        {"queue": "rag_search"},
        "worker_sigaa_biblioteca":      {"queue": "default"},
        "worker_sigaa_extensao":        {"queue": "default"},
        "worker_sigaa_processos":       {"queue": "default"},
    },

    beat_scheduler          = "celery.beat:PersistentScheduler",
    beat_schedule_filename  = "/tmp/celery_beat_schedule",
)
celery_app.autodiscover_tasks([
    'src.application.tasks.tasks_admin',
    'src.application.tasks.ingestion_tasks', # <--- ADICIONE ESTA LINHA!
    'src.application.tasks.process_message_task'
])
logger = logging.getLogger(__name__)


from celery.signals import worker_ready, worker_process_init, worker_process_shutdown

# ─────────────────────────────────────────────────────────────────────────────
# Event loop persistente por processo worker (prefork, single-thread, tasks
# sequenciais dentro de cada processo — ver docker-compose.yml, sem --pool
# explícito nos comandos = default prefork).
#
# Motivo: o orquestrador (orchestration/entrypoint.py) usa um AsyncRedisSaver
# que precisa sobreviver entre mensagens da mesma sessão (HITL via
# interrupt()/Command(resume=...)). Um recurso async assim NÃO pode atravessar
# a fronteira de um asyncio.run() por task (cria "Event loop is closed"/
# "Future attached to a different loop"). A correção é nunca destruir o loop
# entre tasks: criado uma vez aqui, reusado via run_in_worker_loop() em vez de
# asyncio.run(), fechado em on_worker_process_shutdown.
# ─────────────────────────────────────────────────────────────────────────────
_worker_loop: asyncio.AbstractEventLoop | None = None


def run_in_worker_loop(coro):
    """Substitui asyncio.run() nas tasks que precisam de recursos async de
    vida longa (checkpointer do grafo de orquestração). Reusa o mesmo loop
    durante toda a vida do processo worker — nunca cria um loop novo por task."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(coro)


@worker_process_init.connect
def on_worker_process_init(**kwargs):
    """
    Executado quando um novo worker child process é iniciado (prefork).
    Carrega modelos de ML em memória no boot para eliminar latência nas tasks.

    Gated por CELERY_PRELOAD_RERANKER: este signal é global do celery_app —
    dispara em TODO container que sobe um worker deste app, independente de
    --queues. Sem o gate, worker/worker_synthesis/worker_media/worker_graph
    carregavam o CrossEncoder (~90-300MB com torch) mesmo nunca executando a
    task "reranker" (fila rag_search, só consumida por worker_rag). Setar
    CELERY_PRELOAD_RERANKER=true só no worker_rag (docker-compose.yml).
    """
    # Tracing (OpenTelemetry) — sem gate, roda em todo worker (leve, sem
    # lib C pesada) — cada processo Celery precisa do seu próprio
    # TracerProvider (processo separado do FastAPI). NO-OP se
    # settings.ENABLE_TRACING=False.
    try:
        from src.infrastructure.observability.tracing import setup_tracing
        setup_tracing(service_name="oraculo-worker")
    except Exception as e:
        logger.warning("⚠️ [CELERY] Falha ao configurar tracing: %s", e)

    # Config dinâmica (Plano A / Fase 1): espelha `config_dinamica` no Redis
    # deste processo worker — sem gate, são ~7 GET/SET. `dynamic_config.get_*`
    # (síncrono, caminho quente das tasks) passa a ler do espelho sem
    # depender de read-repair. Nunca crítico.
    try:
        from src.infrastructure import dynamic_config
        run_in_worker_loop(dynamic_config.hydrate_redis())
    except Exception as e:
        logger.warning("⚠️ [CELERY] Falha ao hidratar config dinâmica: %s", e)

    # Route/Workflow Registry (Plano A / Fase 2) — mesmo motivo.
    try:
        from src.infrastructure import route_registry
        run_in_worker_loop(route_registry.hydrate_redis())
    except Exception as e:
        logger.warning("⚠️ [CELERY] Falha ao hidratar route_registry: %s", e)

    if os.environ.get("CELERY_PRELOAD_RERANKER", "false").lower() != "true":
        return
    try:
        from src.application.chain.reranker import get_reranker
        logger.info("⏳ [CELERY] Pre-loading ML models on process init...")
        get_reranker()
        logger.info("✅ [CELERY] ML models pre-loaded successfully.")
    except Exception as e:
        logger.error("❌ [CELERY] Failed to pre-load models: %s", e)


@worker_process_shutdown.connect
def on_worker_process_shutdown(**kwargs):
    """
    Executado quando um worker child process (prefork) é encerrado —
    diferente de worker_shutdown, que só dispara no processo principal.
    Fecha os recursos async de vida longa (AsyncRedisSaver do experimento
    LangGraph) e o event loop persistente deste processo, nessa ordem.
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        return
    try:
        from src.application.orchestration.entrypoint import aclose_graph
        _worker_loop.run_until_complete(aclose_graph())
        _worker_loop.run_until_complete(_worker_loop.shutdown_asyncgens())
    except Exception as e:
        logger.error("❌ [CELERY] Falha ao fechar recursos async no shutdown: %s", e)
    finally:
        _worker_loop.close()
        _worker_loop = None

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
# ─────────────────────────────────────────────────────────────────────────────
# Task: atualizar_taxa_brl (periódica — câmbio USD→BRL ao vivo pro /hub/llm-custo)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="atualizar_taxa_brl", bind=True, max_retries=2)
def atualizar_taxa_brl_task(self) -> dict:
    """
    Busca a cotação USD→BRL ao vivo (open.er-api.com, gratuito, sem chave) e
    grava em `admin:usd_brl_rate:auto` (Redis, sem TTL — o valor fica até a
    próxima execução do beat, nunca "expira" pra um fallback ruim no meio do
    dia). NUNCA sobrescreve com dado ruim: se a chamada falhar, mantém o
    último valor bom. Override manual via /hub/llm-custo continua tendo
    precedência — ver `pricing.py::taxa_brl_ativa()`.
    """
    import httpx
    from src.infrastructure.redis_client import get_redis_text

    try:
        resp = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        taxa = data.get("rates", {}).get("BRL")
        if not taxa or taxa <= 0:
            raise ValueError(f"Resposta sem taxa BRL válida: {data}")

        get_redis_text().set("admin:usd_brl_rate:auto", str(taxa))
        logger.info("💱 [FX] Taxa USD→BRL atualizada: %.4f", taxa)
        return {"status": "ok", "taxa": taxa}
    except Exception as e:
        logger.warning("⚠️ [FX] Falha ao atualizar taxa USD→BRL (mantendo valor anterior): %s", e)
        return {"status": "error", "error": str(e)}


@celery_app.task(name="health_check", bind=True)
def health_check_task(self) -> dict:
    """Ping para verificar se o worker está vivo."""
    return {"status": "ok", "worker": self.request.hostname}