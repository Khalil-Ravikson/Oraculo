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
        
        return {
            "status":    "online",
            "sistema":   "Oráculo UEMA",
            "versao":    "5.1.0",
            "redis":     "OK" if redis_ok() else "ERRO",
            "framework": "Cognitive OS (Celery + Redis Streams)",
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
        
        from src.infrastructure.services.intent_seeder_service import IntentSeederService
        seeder = IntentSeederService()
        await seeder.seed()
        logger.info("🌱 [INTENT SEEDER] Configurações e vetores carregados no Redis com sucesso!")
        
        # Resetar flag de Reranker Desabilitado no boot
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.delete("reranker:status")
        logger.info("✅ Status do Reranker resetado no Redis (pronto para tentar carregar)")
        
    except Exception as exc:
        logger.error("❌ Falha crítica no Redis/Seeder: %s", exc)
        if not settings.DEV_MODE:
            raise RuntimeError("Redis e Seeder são obrigatórios para o funcionamento.")

    # 2. IA: Pré-aquecimento de Embeddings e Chain
    try:
        from src.rag.embeddings import get_embeddings
        
        # Singleton de Embeddings (Google Gemini)
        _ = get_embeddings().embed_query("teste de aquecimento")
        logger.info("✅ Modelo de Embeddings carregado")
        # 🔥 ADICIONE ESTE BLOCO AQUI PARA O PRÉ-AQUECIMENTO DOS WORKERS 👇
        logger.info("⚙️  Fazendo Autodiscovery dos Workers do Cognitive OS...")
        from src.application.workers.registry import _autodiscover_workers, available
        _autodiscover_workers()
        logger.info("✅ Workers carregados na RAM: %s", available())


        # A OracleChain não existe mais! O Cognitive OS é invocado sob demanda via Celery.
        logger.info("✅ Arquitetura Multi-Agente (Cognitive OS) pronta")
    except Exception as exc:
        logger.warning("⚠️  Falha ao pré-aquecer componentes de IA: %s", exc)

    # 2b. Agent Registry (Fase 2/5 do PLANO_REFATORACAO_SUPERVISOR.md)
    try:
        from src.agents.bootstrap import register_all_agents
        from src.agents.registry import registry
        register_all_agents()
        logger.info("✅ [AGENT REGISTRY] Agentes disponíveis: %s", [a.name for a in registry.all()])
    except Exception as exc:
        logger.warning("⚠️  Falha ao registrar agentes: %s", exc)

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
    # 1. Interface Web (Frontend) e Rotas Centralizadas (ChunkViz + Eval)
    from src.api.routers.web.hub import router as hub_router
    
    # 2. Administração (Admin REST)
    from src.api.routers.admin.admin_users_api import router as users_router
    from src.api.routers.admin.admin_api       import router as admin_api_router
    
    # 👇 1. ADICIONE APENAS ESTA LINHA AQUI 👇
    from src.application.webhook.webhook_controller import router as webhook_router

    # NOTA ARQUITETURAL: 
    # O eval_api.py e o chunkviz_tools.py são apenas "Cérebros" (Lógica de Negócio).
    # Eles não possuem mais 'router'. Todas as rotas deles estão no hub_router.

    # Registrando no FastAPI com os prefixos e tags
    app.include_router(users_router, prefix="/api/admin/users", tags=["Admin: Usuários"])
    
    app.include_router(admin_api_router)
    
    # O hub_router agora carrega todas as rotas web (/, /hub, /hub/chunkviz/..., /eval/...)
    app.include_router(hub_router)

    # 👇 2. E ADICIONE APENAS ESTA LINHA AQUI 👇
    app.include_router(webhook_router)

# ── Instanciação da Aplicação ─────────────────────────────────────────────────
app = create_app()