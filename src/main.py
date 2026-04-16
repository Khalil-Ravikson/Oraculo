"""
src/main.py — v4.2 (startup 100% async, sem MemorySaver)
=========================================================
"""
from __future__ import annotations

import logging
import os
import traceback

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Grafo compilado — inicializado no startup, acessado via get_compiled_graph()
# NÃO importar o grafo aqui: importação tardia evita circular imports no startup


def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Académico Inteligente da UEMA",
        version     = "4.2.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,
        redoc_url   = None,
    )

    _montar_static(app)
    _registrar_routers(app)

    # Prometheus /metrics (no-op se prometheus_client não instalado)
    try:
        from prometheus_client import make_asgi_app
        app.mount("/metrics", make_asgi_app())
    except ImportError:
        pass

    @app.on_event("startup")
    async def on_startup() -> None:
        await _startup(settings)

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await _shutdown()

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/hub/")

    @app.get("/health", tags=["Sistema"])
    async def health():
        from src.infrastructure.redis_client import redis_ok
        return {
            "status":   "online",
            "sistema":  "Oráculo UEMA",
            "versao":   "4.2.0",
            "redis_ok": redis_ok(),
        }

    return app


async def _startup(settings) -> None:
    """Pipeline de startup — cada etapa tem tratamento de erro independente."""
    logging.basicConfig(
        level   = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format  = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt = "%H:%M:%S",
    )
    logger.info("🚀 Oráculo UEMA v4.2 iniciando...")

    # ── 1. Índices Redis (SVS-VAMANA / HNSW) ──────────────────────────────────
    from src.infrastructure.redis_client import inicializar_indices
    await inicializar_indices()

    # ── 2. Modelo de Embeddings (singleton — ~3s na primeira carga) ───────────
    from src.rag.embeddings import get_embeddings
    embeddings = get_embeddings()
    logger.info("✅ Embeddings prontos (provider=%s).", settings.EMBEDDING_PROVIDER)

    # ── 3. SemanticRouter (async Redis nativo) ────────────────────────────────
    import redis.asyncio as aioredis
    from src.domain.services.semantic_router import SemanticRouterService

    async_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    semantic_router = SemanticRouterService(
        async_redis      = async_redis,
        embeddings_model = embeddings,
    )
    logger.info("✅ SemanticRouterService pronto.")

    # ── 4. PydanticRouter (Gemini structured output) ──────────────────────────
    from src.rag.query.pydantic_router import PydanticRouter
    pydantic_router = PydanticRouter()
    logger.info("✅ PydanticRouter pronto.")

    # ── 5. OraculoRouterService (orquestra as camadas 3 e 4) ──────────────────
    from src.domain.services.oraculo_router import OraculoRouterService
    oraculo_router = OraculoRouterService(
        semantic_router = semantic_router,
        pydantic_router = pydantic_router,
    )
    logger.info("✅ OraculoRouterService pronto.")

    # ── 6. LangGraph com AsyncRedisSaver ──────────────────────────────────────
    from src.application.graph.builder import init_graph
    try:
        await init_graph(oraculo_router)
    except Exception as e:
        raise e

    # ── 7. Evolution API (não-fatal em DEV) ────────────────────────────────────
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
        logger.info("✅ Evolution API inicializada.")
    except Exception as exc:
        logger.warning("⚠️  Evolution API indisponível (DEV?): %s", exc)

    logger.info("🟢 Oráculo UEMA pronto para receber mensagens.")


async def _shutdown() -> None:
    """Libera recursos na ordem inversa da inicialização."""
    logger.info("🛑 Iniciando shutdown...")

    # Fecha AsyncRedisSaver do LangGraph
    try:
        from src.application.graph.builder import aclose_checkpointer
        await aclose_checkpointer()
    except Exception:
        pass

    # Flush Langfuse (se configurado)
    try:
        from src.infrastructure.observability.langfuse_client import flush_langfuse
        flush_langfuse()
    except Exception:
        pass

    logger.info("🛑 Oráculo UEMA encerrado.")


def _montar_static(app: FastAPI) -> None:
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _registrar_routers(app: FastAPI) -> None:
    from src.api.hub           import router as hub_router
    from src.api.admin_api     import router as admin_api_router
    from src.api.rag_admin     import router as rag_admin_router
    from src.api               import monitor
    from src.api.chunkviz_api  import router as chunkviz_router
    from src.api.routers.webhook import router as webhook_router

    app.include_router(hub_router)
    app.include_router(admin_api_router)
    app.include_router(rag_admin_router)
    app.include_router(monitor.router, prefix="/monitor")
    app.include_router(chunkviz_router)
    app.include_router(webhook_router, prefix="/api/v1")

    try:
        from src.infrastructure.settings import settings
        if settings.DEV_MODE:
            from src.api.eval_dashboard import router as eval_router
            app.include_router(eval_router, prefix="/eval", tags=["Eval"])
    except Exception:
        pass


app = create_app()