"""
src/main.py — v4.1 (async startup correto)
==========================================

CORREÇÕES vs v4.0:
  - inicializar_dependencias() tornou-se async (era sync com new_event_loop hack)
  - inicializar_indices() chamado com await (agora é coroutine)
  - OraculoRouterService recebe embeddings_model por DI
  - Remoção do asyncio.new_event_loop() dentro de contexto async (RuntimeError)
  - _startup() aguarda inicializar_dependencias() correctamente
"""
from __future__ import annotations

import logging
import os
import traceback

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Grafo compilado — partilhado entre requests (thread-safe, LangGraph é imutável)
oraculo_graph = None


# ─────────────────────────────────────────────────────────────────────────────
# Inicialização de dependências (ASYNC — chamado uma vez no startup)
# ─────────────────────────────────────────────────────────────────────────────

async def inicializar_dependencias() -> None:
    """
    Monta o container de DI e compila o LangGraph.
    DEVE ser chamado com await dentro de um contexto async.

    ORDEM OBRIGATÓRIA:
      1. Índices Redis → devem existir antes de qualquer query
      2. Embeddings   → modelo carregado em memória (pode demorar ~3s)
      3. SemanticRouter  → indexa routes no Redis (usa embeddings)
      4. PydanticRouter  → apenas valida settings, sem I/O
      5. OraculoRouter   → orquestra as camadas acima
      6. LangGraph       → compila o grafo com o router injectado
    """
    logger.info("⚙️  [STARTUP] Inicializando dependências async...")

    # 1. Índices Redis (async — cria SVS-VAMANA se não existir)
    from src.infrastructure.redis_client import inicializar_indices
    await inicializar_indices()

    # 2. Modelo de embeddings (singleton — carregado uma vez)
    from src.rag.embeddings import get_embeddings
    embeddings = get_embeddings()
    logger.info("✅ [STARTUP] Embeddings prontos.")

    # 3. SemanticRouter (instanciado sync — indexa no Redis via __init__)
    #    O SemanticRouter do redisvl 0.17.0 não tem __init__ async.
    #    A indexação inicial das routes é feita no construtor de forma síncrona.
    #    Justificado: acontece UMA vez no startup, não em runtime.
    from src.domain.services.oraculo_router import OraculoRouterService
    from src.rag.query.pydantic_router import PydanticRouter

    logger.info("⚙️  [STARTUP] Instanciando PydanticRouter...")
    pydantic_router = PydanticRouter()

    logger.info("⚙️  [STARTUP] Instanciando OraculoRouterService (SemanticRouter + Pydantic)...")
    oraculo_router = OraculoRouterService(
        pydantic_router  = pydantic_router,
        embeddings_model = embeddings,
    )

    # 4. Compila o LangGraph
    from src.application.graph.builder import compilar_grafo
    logger.info("⚙️  [STARTUP] Compilando grafo LangGraph...")
    global oraculo_graph
    oraculo_graph = compilar_grafo(oraculo_router)
    logger.info("✅ [STARTUP] Grafo compilado. Oráculo pronto.")


# ─────────────────────────────────────────────────────────────────────────────
# Factory da aplicação FastAPI
# ─────────────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Académico Inteligente da UEMA",
        version     = "4.1.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,
        redoc_url   = None,
    )

    # Prometheus /metrics (no-op se prometheus_client não instalado)
    from src.infrastructure.observability.metrics import setup_metrics
    setup_metrics(app)

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
        return {
            "status":   "online",
            "sistema":  "Oráculo UEMA",
            "versao":   "4.1.0",
            "redis_ok": redis_ok(),
        }

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Startup completo
# ─────────────────────────────────────────────────────────────────────────────

async def _startup(settings) -> None:
    # Configura logging
    logging.basicConfig(
        level   = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format  = "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt = "%H:%M:%S",
    )

    logger.info("🚀 Oráculo UEMA v4.1 iniciando...")

    # RAG chunks gauge (Prometheus)
    try:
        from src.infrastructure.observability.metrics import update_rag_chunks
        update_rag_chunks()
    except Exception:
        pass

    # Inicializa dependências (AWAIT — a função agora é coroutine)
    try:
        await inicializar_dependencias()
    except Exception:
        logger.error(
            "🚨 ERRO CRÍTICO ao inicializar dependências:\n%s",
            traceback.format_exc(),
        )
        # Falha no startup é fatal — o bot não funciona sem Redis + Grafo
        raise RuntimeError(
            "Falha no startup. Verifique os logs acima para o erro exato."
        )

    # WhatsApp gateway (não-fatal — bot funciona sem WhatsApp em modo dev)
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
        logger.info("✅ Evolution API inicializada.")
    except Exception as exc:
        logger.warning(
            "⚠️  Evolution API indisponível (modo dev?): %s", exc
        )

    logger.info("✅ Oráculo UEMA pronto para receber mensagens.")


def _shutdown() -> None:
    try:
        from src.infrastructure.observability.langfuse_client import flush_langfuse
        flush_langfuse()
    except Exception:
        pass
    logger.info("🛑 Oráculo UEMA encerrado.")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de montagem
# ─────────────────────────────────────────────────────────────────────────────

def _montar_static(app: FastAPI) -> None:
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _registrar_routers(app: FastAPI) -> None:
    from src.api.hub        import router as hub_router
    from src.api.admin_api  import router as admin_api_router
    from src.api.rag_admin  import router as rag_admin_router
    from src.api            import monitor
    from src.api.chunkviz_api import router as chunkviz_router

    app.include_router(hub_router)
    app.include_router(admin_api_router)
    app.include_router(rag_admin_router)
    app.include_router(monitor.router, prefix="/monitor")
    app.include_router(chunkviz_router)

    # Eval dashboard (só em DEV)
    try:
        from src.infrastructure.settings import settings
        if settings.DEV_MODE:
            from src.api.eval_dashboard import router as eval_router
            app.include_router(eval_router, prefix="/eval", tags=["Eval"])
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

app = create_app()