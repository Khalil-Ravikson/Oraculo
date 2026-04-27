"""
src/main.py — v5 (LangChain Runnables, sem LangGraph)
=======================================================

MUDANÇAS vs v4:
  - Removido: compilar_grafo(), LangGraph, OraculoRouterService complexo
  - Adicionado: OracleChain (pipeline linear simples)
  - Adicionado: setup_logging() com stdout sem buffer (resolve logs mudos no Docker)
  - Simplificado: startup em <1s vs ~5s anterior
"""
from __future__ import annotations

import logging
import os
import traceback

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response as FastAPIResponse
# ── Logging PRIMEIRO — antes de qualquer import src.* ─────────────────────────
from src.infrastructure.logging_config import setup_logging
from src.infrastructure.observability.metrics import PrometheusMetrics

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    # Configura logging imediatamente
    setup_logging(level=settings.LOG_LEVEL)

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Académico Inteligente da UEMA",
        version     = "5.0.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,
        redoc_url   = None,
    )

    _montar_static(app)
    _registrar_routers(app)

    @app.on_event("startup")
    async def on_startup():
        await _startup(settings)

    @app.on_event("shutdown")
    async def on_shutdown():
        _shutdown()

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/hub/")

    @app.get("/health", tags=["Sistema"])
    async def health():
        from src.infrastructure.redis_client import redis_ok
        from src.application.chain.oracle_chain import get_oracle_chain
        chain_ok = True
        try:
            get_oracle_chain()
        except Exception:
            chain_ok = False
        return {
            "status":    "online",
            "sistema":   "Oráculo UEMA",
            "versao":    "5.0.0",
            "redis_ok":  redis_ok(),
            "chain_ok":  chain_ok,
            "framework": "LangChain Runnables",
        }

    

# 👇👇👇 ADICIONE ESTA NOVA ROTA AQUI 👇👇👇
    @app.get("/metrics", tags=["Observabilidade"], include_in_schema=False)
    async def prometheus_metrics():
        from src.infrastructure.observability.metrics import PrometheusMetrics
        body, ct = PrometheusMetrics().generate_latest_output()
        return FastAPIResponse(content=body, media_type=ct)
# 👆👆👆 -------------------------------- 👆👆👆
    return app

async def _startup(settings) -> None:
    logger.info("🚀 Oráculo UEMA v5 iniciando (LangChain Runnables)...")

    # 1. Inicializa índices Redis (async)
    try:
        from src.infrastructure.redis_client import inicializar_indices
        await inicializar_indices()
        logger.info("✅ Índices Redis OK")
    except Exception as exc:
        logger.error("❌ Redis indices falhou: %s\n%s", exc, traceback.format_exc())
        raise RuntimeError(f"Redis indisponível: {exc}") from exc

    # 2. Pré-aquece o modelo de embeddings (lazy singleton)
    try:
        from src.rag.embeddings import get_embeddings
        emb = get_embeddings()
        # Teste rápido para validar que o modelo funciona
        _ = emb.embed_query("teste")
        logger.info("✅ Embeddings OK")
    except Exception as exc:
        logger.warning("⚠️  Embeddings falhou no pré-aquecimento: %s", exc)

    # 3. Inicializa a chain (singleton)
    try:
        from src.application.chain.oracle_chain import get_oracle_chain
        get_oracle_chain()
        logger.info("✅ OracleChain (LangChain) pronta")
    except Exception as exc:
        logger.error("❌ Chain falhou: %s", exc)
        raise

    # 4. WhatsApp gateway (não-fatal em dev)
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
        logger.info("✅ Evolution API inicializada")
    except Exception as exc:
        logger.warning("⚠️  Evolution API indisponível (modo dev?): %s", exc)

    logger.info("✅ Oráculo UEMA v5 pronto! Framework: LangChain Runnables")


def _shutdown() -> None:
    logger.info("🛑 Oráculo UEMA encerrando...")


def _montar_static(app: FastAPI) -> None:
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")





def _registrar_routers(app: FastAPI) -> None:
    from src.api.hub         import router as hub_router
    from src.api.admin_api   import router as admin_api_router
    from src.api.rag_admin   import router as rag_admin_router
    from src.api             import monitor
    from src.api.chunkviz_api import router as chunkviz_router
    from src.api.eval_api    import router as eval_router

    app.include_router(hub_router)
    app.include_router(admin_api_router)
    app.include_router(rag_admin_router)
    app.include_router(monitor.router, prefix="/monitor")
    app.include_router(chunkviz_router)
    app.include_router(eval_router, prefix="/eval", tags=["Eval RAG"])


app = create_app()