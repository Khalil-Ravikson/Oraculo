"""
src/main.py — v4 (Prometheus metrics + RAG Admin + Langfuse flush)
==================================================================
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Acadêmico Inteligente da UEMA",
        version     = "4.0.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,
        redoc_url   = None,
    )

    # ── Prometheus metrics endpoint ───────────────────────────────────────────
    from src.infrastructure.observability.metrics import setup_metrics
    setup_metrics(app)

    # ── Arquivos estáticos ────────────────────────────────────────────────────
    _montar_static(app)

    # ── Routers ───────────────────────────────────────────────────────────────
    _registrar_routers(app)

    # ── Eventos ───────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        await _startup(settings)

    @app.on_event("shutdown")
    async def shutdown():
        _shutdown()

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/hub/")

    @app.get("/health", tags=["Sistema"])
    async def health():
        from src.infrastructure.redis_client import redis_ok
        return {
            "status":   "online",
            "sistema":  "Oráculo UEMA",
            "versao":   "4.0.0",
            "redis_ok": redis_ok(),
        }

    return app


def _montar_static(app: FastAPI) -> None:
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _registrar_routers(app: FastAPI) -> None:
    from src.api.webhook  import router as webhook_router
    from src.api.hub      import router as hub_router
    from src.api.admin_api import router as admin_api_router
    from src.api.rag_admin import router as rag_admin_router
    from src.api import monitor

    app.include_router(hub_router)
    app.include_router(admin_api_router)
    app.include_router(rag_admin_router)  # ← RAG admin com auth JWT
    app.include_router(webhook_router, prefix="/api/v1", tags=["Webhook"])
    app.include_router(monitor.router, prefix="/monitor")

    try:
        from src.infrastructure.settings import settings
        from src.api.eval_dashboard import router as eval_router
        if settings.DEV_MODE:
            app.include_router(eval_router, prefix="/eval", tags=["Eval"])
    except Exception:
        pass


async def _startup(settings) -> None:
    logging.basicConfig(
        level   = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format  = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    logger.info("🚀 Oráculo UEMA v4.0 iniciando...")

    erros = settings.validar_producao()
    for e in erros:
        if settings.DEV_MODE:
            logger.warning("⚠️  [DEV] %s", e)
        else:
            logger.error("❌ [PROD] %s", e)

    # Redis
    try:
        from src.infrastructure.redis_client import inicializar_indices
        inicializar_indices()
    except Exception as e:
        logger.error("❌ Redis offline: %s", e)

    # RAG chunks metric inicial
    try:
        from src.infrastructure.observability.metrics import update_rag_chunks
        update_rag_chunks()
    except Exception:
        pass

    # Evolution API
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
    except Exception as e:
        logger.warning("⚠️  Evolution API: %s", e)

    logger.info("✅ Oráculo UEMA pronto!")


def _shutdown() -> None:
    """Flush Langfuse antes de encerrar."""
    try:
        from src.infrastructure.observability.langfuse_client import flush_langfuse
        flush_langfuse()
    except Exception:
        pass


app = create_app()