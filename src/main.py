# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 10: src/main.py — Container DI e startup completo
# ─────────────────────────────────────────────────────────────────────────────

"""
main.py — FastAPI app com container DI completo para esta sprint
================================================================
Ordem de inicialização (importa para evitar race conditions no startup):

  1. Configurações e logging
  2. Redis indices (idempotente)
  3. Embeddings model (CPU, singleton)
  4. RedisVL adapters (injectados via DI)
  5. Semantic Cache + Session Manager
  6. LLM clients (Gemini)
  7. Router pipeline (Semantic + Pydantic)
  8. LangGraph (compilado com HITL)
  9. Prometheus metrics
  10. FastAPI routes (webhook + hub + metrics)
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response

from src.infrastructure.settings import settings

# ─── Logging de produção ──────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.DEBUG if settings.DEV_MODE else logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    handlers= [logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─── Container global ─────────────────────────────────────────────────────────
# Guardamos as instâncias aqui para reutilização entre requests.

_CONTAINER: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup e shutdown da aplicação.
    O lifespan substitui @app.on_event("startup") no FastAPI 0.110+.
    """
    logger.info("🚀 [STARTUP] Oráculo UEMA iniciando...")

    # 1. Redis indices (SVS-VAMANA)
    from src.infrastructure.redis_client import (
        inicializar_indices, get_redis_text,
    )
    await inicializar_indices()
   
    _CONTAINER["redis_text"] = get_redis_text()
    logger.info("✅ [STARTUP] Redis conectado.")

    # 2. Embeddings model (singleton — carregado uma vez)
    from src.rag.embeddings import get_embeddings
    embeddings = get_embeddings()
    _CONTAINER["embeddings"] = embeddings
    logger.info("✅ [STARTUP] Modelo de embeddings carregado.")

    # 3. RedisVL adapters
    from src.infrastructure.adapters.redis_vector_adapter import RedisVLVectorAdapter
    vector_adapter = RedisVLVectorAdapter(embeddings_model=embeddings)
    _CONTAINER["vector_adapter"] = vector_adapter
    logger.info("✅ [STARTUP] RedisVLVectorAdapter pronto.")

    # 4. Semantic Cache
    from src.infrastructure.cache.llm_cache import OracloSemanticCache
    llm_cache = OracloSemanticCache(embeddings_model=embeddings)
    _CONTAINER["llm_cache"] = llm_cache
    logger.info("✅ [STARTUP] SemanticCache pronto.")

    # 5. Session Manager
    from src.memory.adapters.redisvl_session_manager import RedisVLSessionManager
    session_manager = RedisVLSessionManager()
    _CONTAINER["session_manager"] = session_manager
    logger.info("✅ [STARTUP] SessionManager pronto.")


    # 7. Router pipeline
    from src.rag.query.pydantic_router import PydanticRouter
    from src.domain.services.oraculo_router import OraculoRouterService
    pydantic_router = PydanticRouter()
    oraculo_router  = OraculoRouterService(
        pydantic_router  = pydantic_router,
        embeddings_model = embeddings,
    )
    _CONTAINER["oraculo_router"] = oraculo_router
    logger.info("✅ [STARTUP] OraculoRouterService pronto.")


    
    # 10. LangGraph
    from src.application.graph.builder import compilar_grafo
    graph = compilar_grafo(
        oraculo_router  = oraculo_router,
        vector_adapter  = vector_adapter,
        llm_cache       = llm_cache,
        session_manager = session_manager,
    )
    _CONTAINER["graph"] = graph
    logger.info("✅ [STARTUP] LangGraph compilado.")

    # 11. Prometheus
    from src.infrastructure.observability.metrics import PrometheusMetrics
    metrics = PrometheusMetrics(namespace="oraculo")
    _CONTAINER["metrics"] = metrics


    logger.info("✅ [STARTUP] Webhook registado.")

    logger.info("🎯 [STARTUP] Oráculo UEMA pronto para receber mensagens.")
    yield   # ← aplicação a correr

    # Shutdown
    logger.info("🛑 [SHUTDOWN] Oráculo encerrando...")
    _CONTAINER.clear()


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Oráculo UEMA",
    version     = "3.0.0",
    description = "RAG Enterprise para a Universidade Estadual do Maranhão",
    lifespan    = lifespan,
    docs_url    = "/docs" if settings.DEV_MODE else None,
    redoc_url   = None,
)


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """Endpoint Prometheus — acessível pelo scraper, não pelo utilizador."""
    m = _CONTAINER.get("metrics")
    if m is None:
        return Response("# not ready\n", media_type="text/plain")
    body, content_type = m.generate_latest_output()
    return Response(content=body, media_type=content_type)


@app.get("/health")
async def health():
    from src.infrastructure.redis_client import redis_ok
    return {
        "status": "healthy",
        "redis":  redis_ok(),
        "graph":  "graph" in _CONTAINER,
    }