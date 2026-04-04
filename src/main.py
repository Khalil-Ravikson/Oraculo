# src/main.py
"""
Oráculo UEMA — Entrypoint FastAPI v3.

ESTRUTURA:
  /                     → redirect para /hub/
  /hub/*                → Portal Admin (MVC com Jinja2)
  /api/admin/*          → REST API admin (JWT)
  /api/v1/evolution/webhook → Webhook WhatsApp
  /health               → Health check
  /static/*             → Arquivos estáticos (CSS, JS)

STARTUP:
  1. Valida configurações críticas (.env)
  2. Inicializa índices Redis (idempotente)
  3. Verifica conectividade Redis + PostgreSQL
  4. Inicializa Evolution API (webhook setup)
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Acadêmico Inteligente da UEMA",
        version     = "3.0.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,  # esconde em produção
        redoc_url   = None,
    )

    # ── Arquivos estáticos ────────────────────────────────────────────────────
    _montar_static(app)

    # ── Routers ───────────────────────────────────────────────────────────────
    _registrar_routers(app)

    # ── Eventos de startup ────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        await _startup(settings)

    # ── Root redirect ─────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/hub/")

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get("/health", tags=["Sistema"])
    async def health():
        from src.infrastructure.redis_client import redis_ok
        return {
            "status":   "online",
            "sistema":  "Oráculo UEMA",
            "versao":   "3.0.0",
            "redis_ok": redis_ok(),
        }

    return app


def _montar_static(app: FastAPI) -> None:
    """Monta arquivos estáticos com fallback gracioso."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    else:
        logger.warning("⚠️  Pasta 'static/' não encontrada — criando...")
        os.makedirs(static_dir, exist_ok=True)
        app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _registrar_routers(app: FastAPI) -> None:
    """Registra todos os routers da aplicação."""
    from src.api.webhook  import router as webhook_router
    from src.api.hub      import router as hub_router
    from src.api.admin_api import router as admin_api_router

    # Portal admin (MVC)
    app.include_router(hub_router)

    # REST API admin (JWT)
    app.include_router(admin_api_router)

    # Webhook WhatsApp
    app.include_router(webhook_router, prefix="/api/v1", tags=["Webhook"])

    # Monitor SSE (opcional — apenas em DEV)
    try:
        from src.api.eval_dashboard import router as eval_router
        from src.infrastructure.settings import settings
        if settings.DEV_MODE:
            app.include_router(eval_router, prefix="/eval", tags=["Eval"])
    except Exception:
        pass


async def _startup(settings) -> None:
    """Inicialização do sistema — chamada no startup do FastAPI."""
    logging.basicConfig(
        level   = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format  = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    logger.info("🚀 Oráculo UEMA v3.0 iniciando...")

    # Valida configurações
    erros = settings.validar_producao()
    for e in erros:
        if settings.DEV_MODE:
            logger.warning("⚠️  [DEV] %s", e)
        else:
            logger.error("❌ [PROD] %s", e)

    # Inicializa Redis
    try:
        from src.infrastructure.redis_client import inicializar_indices
        inicializar_indices()
        logger.info("✅ Índices Redis inicializados.")
    except Exception as e:
        logger.error("❌ Redis offline no startup: %s", e)

    # Inicializa Evolution API
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
        logger.info("✅ Evolution API inicializada.")
    except Exception as e:
        logger.warning("⚠️  Evolution API startup falhou: %s", e)

    logger.info("✅ Oráculo UEMA pronto!")


# Entry point
app = create_app()