"""
src/main.py — v5.1 (Versão Final Consolidada)
=============================================
Arquitetura: Clean Architecture (Interface Adapters)
Funcionalidade: Orquestração da API, Observabilidade e Ciclo de Vida
"""
from __future__ import annotations

import logging
import os
import traceback
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

# ── Logging PRIMEIRO ──────────────────────────────────────────────────────────
from src.infrastructure.logging_config import setup_logging

logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    from src.infrastructure.settings import settings

    # 1. Configuração imediata de logs (stdout sem buffer para Docker)
    setup_logging(level=settings.LOG_LEVEL)

    app = FastAPI(
        title       = "Oráculo UEMA",
        description = "Assistente Académico Inteligente da UEMA",
        version     = "5.1.0",
        docs_url    = "/api/docs" if settings.DEV_MODE else None,
        redoc_url   = None,
    )

    # 2. Prometheus Instrumentator (Métricas de RPM e Latência)
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator().instrument(app)

    # 3. Montagem de ficheiros estáticos e registo de rotas
    _montar_static(app)
    _registrar_routers(app)

    @app.on_event("startup")
    async def on_startup():
        # Expõe /metrics (une métricas do FastAPI + métricas customizadas de Tokens/Custo)
        instrumentator.expose(app, endpoint="/metrics", include_in_schema=False)
        await _startup(settings)

    @app.on_event("shutdown")
    async def on_shutdown():
        _shutdown()

    # ── Rotas de Sistema ──────────────────────────────────────────────────────

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/hub/")

    @app.get("/health", tags=["Sistema"])
    async def health():
        """Verificação de saúde de todos os serviços críticos."""
        from src.infrastructure.redis_client import redis_ok
        from src.application.chain.oracle_chain import get_oracle_chain
        
        chain_status = True
        try:
            get_oracle_chain()
        except Exception:
            chain_status = False

        return {
            "status":    "online",
            "sistema":   "Oráculo UEMA",
            "versao":    "5.1.0",
            "redis":     "OK" if redis_ok() else "ERRO",
            "chain":     "OK" if chain_status else "ERRO",
            "framework": "LangChain Runnables",
        }

    return app

# ── Ciclo de Vida (Startup / Shutdown) ────────────────────────────────────────

async def _startup(settings) -> None:
    logger.info("🚀 A iniciar Oráculo UEMA (v5.1)...")

    # 1. Redis: Inicialização de Índices (Busca Híbrida e Vetorial)
    try:
        from src.infrastructure.redis_client import inicializar_indices
        await inicializar_indices()
        logger.info("✅ Índices Redis inicializados (SVS-VAMANA)")
    except Exception as exc:
        logger.error("❌ Falha crítica no Redis: %s", exc)
        # Em produção, isto deve impedir o arranque
        if not settings.DEV_MODE:
            raise RuntimeError("Redis obrigatório para RAG não disponível.")

    # 2. IA: Pré-aquecimento de Embeddings e Chain
    try:
        from src.rag.embeddings import get_embeddings
        from src.application.chain.oracle_chain import get_oracle_chain
        
        # Singleton de Embeddings (Google Gemini)
        _ = get_embeddings().embed_query("teste de aquecimento")
        logger.info("✅ Modelo de Embeddings carregado")
        
        # Singleton da Chain (Pipeline RAG)
        get_oracle_chain()
        logger.info("✅ OracleChain pronta para inferência")
    except Exception as exc:
        logger.warning("⚠️  Falha ao pré-aquecer componentes de IA: %s", exc)

    # 3. Gateway WhatsApp (Evolution API)
    try:
        from src.services.evolution_service import EvolutionService
        await EvolutionService().inicializar()
        logger.info("✅ Gateway WhatsApp (Evolution) ativo")
    except Exception as exc:
        logger.warning("⚠️  Evolution API offline (Modo Web apenas): %s", exc)

    logger.info("✅ Oráculo pronto para receber mensagens!")


def _shutdown() -> None:
    logger.info("🛑 A encerrar Oráculo UEMA...")


def _montar_static(app: FastAPI) -> None:
    """Configura o diretório de ficheiros estáticos (CSS, JS, Imagens)."""
    static_path = os.path.join(os.path.dirname(__file__), "..", "static")
    os.makedirs(static_path, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_path), name="static")


def _registrar_routers(app: FastAPI) -> None:
    """
    IMPORTAÇÕES CORRIGIDAS: 
    Mantendo os nomes originais dos arquivos, mas apontando para as novas pastas.
    """
    # 1. Interface Web (Frontend)
    from src.api.routers.web.hub import router as hub_router
    
    # 2. Administração (Admin) - Caminhos completos das novas pastas
    from src.api.routers.admin.admin_users_api import router as users_router
    from src.api.routers.admin.admin_api       import router as admin_api_router
    from src.api.routers.admin.eval_dashboard  import router as eval_dash_router
    from src.api.routers.admin.eval_api        import router as eval_api_router
    
    # 3. Ferramentas (Tools)
    from src.api.routers.tools.chunkviz_api    import router as chunkviz_router

    # Registrando no FastAPI com os prefixos e tags
    app.include_router(users_router, prefix="/api/admin/users", tags=["Admin: Usuários"])
    app.include_router(hub_router)
    app.include_router(admin_api_router)
    app.include_router(chunkviz_router, prefix="/tools", tags=["Tools: Chunkviz"])   
    app.include_router(eval_dash_router, prefix="/eval", tags=["Admin: Eval GUI"])
    app.include_router(eval_api_router, prefix="/eval/api", tags=["Admin: Eval API"])

# ── Instanciação da Aplicação ─────────────────────────────────────────────────
app = create_app()