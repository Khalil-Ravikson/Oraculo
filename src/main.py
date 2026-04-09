"""
src/main.py — v4 (LangGraph + Roteamento Duplo [Semântico+Pydantic] + FastAPI)
================================================================================
"""
from __future__ import annotations

import logging
import os
import traceback

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# =================================================================
# VARIÁVEL GLOBAL DO SEU AGENTE (O cérebro do LangGraph)
# =================================================================
oraculo_graph = None 

def inicializar_dependencias():
    """Configura o Roteador Mestre e compila o LangGraph."""
    print("⚙️ [STARTUP] Inicializando dependências do LangGraph...")
    
    from src.infrastructure.redis_client import get_redis
    from src.rag.embeddings import get_embeddings
    from src.application.graph.builder import compilar_grafo
    
    # --- IMPORTS FLEXÍVEIS (Evita quebrar se a pasta mudou) ---
    from src.domain.services.semantic_router import SemanticRouterService

        
    from src.rag.query.pydantic_router import PydanticRouter
    from src.domain.services.oraculo_router import OraculoRouterService
    
    redis_client = get_redis()
    embeddings = get_embeddings()
    
    print("⚙️ [STARTUP] Instanciando a 1ª e 2ª Linhas de Roteamento...")
    semantic_router = SemanticRouterService(redis_client, embeddings)
    pydantic_router = PydanticRouter()
    
    print("⚙️ [STARTUP] Criando o Orquestrador Mestre...")
    oraculo_router = OraculoRouterService(semantic_router, pydantic_router)
    
    print("⚙️ [STARTUP] Compilando o Grafo LangGraph...")
    global oraculo_graph
    oraculo_graph = compilar_grafo(oraculo_router)
    print("✅ [STARTUP] Grafo compilado e injetado com sucesso!")


def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Acadêmico Inteligente da UEMA",
        version     = "4.0.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,
        redoc_url   = None,
    )

    from src.infrastructure.observability.metrics import setup_metrics
    setup_metrics(app)

    _montar_static(app)
    _registrar_routers(app)

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
    from src.api.hub       import router as hub_router
    from src.api.admin_api import router as admin_api_router
    from src.api.rag_admin import router as rag_admin_router
    from src.api import monitor

    app.include_router(hub_router)
    app.include_router(admin_api_router)
    app.include_router(rag_admin_router)
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

    # 1. Inicializa o Redis Indices
    try:
        from src.infrastructure.redis_client import inicializar_indices
        inicializar_indices()
    except Exception as e:
        logger.error("❌ Redis offline: %s", e)

    # 2. Inicializa RAG Metrics
    try:
        from src.infrastructure.observability.metrics import update_rag_chunks
        update_rag_chunks()
    except Exception:
        pass

    # 3. CHAMA NOSSA NOVA FUNÇÃO DO LANGGRAPH AQUI!
    # Nota: Removi o try...except que "engolia" o erro. Se quebrar, vamos ver na hora.
    try:
        inicializar_dependencias()
    except Exception as e:
        print("\n" + "="*60)
        print("🚨 ERRO CRÍTICO AO COMPILAR O CÉREBRO DO ORÁCULO 🚨")
        traceback.print_exc()
        print("="*60 + "\n")
        # Forçamos a queda do FastAPI, pois não adianta subir a API com o bot quebrado.
        raise RuntimeError("Falha no LangGraph. Veja o terminal acima para os detalhes exatos.") from e

    # 4. Evolution API
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
    except Exception as e:
        logger.warning("⚠️  Evolution API: %s", e)

    logger.info("✅ Oráculo UEMA pronto!")


def _shutdown() -> None:
    try:
        from src.infrastructure.observability.langfuse_client import flush_langfuse
        flush_langfuse()
    except Exception:
        pass

app = create_app()