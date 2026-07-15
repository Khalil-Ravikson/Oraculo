"""
src/agents/bootstrap.py
==========================
Registro explícito dos agentes no `AgentRegistry` (ver agents/registry.py,
Fase 2). Chamado uma vez no startup do FastAPI (`src/main.py`).

Por que explícito e não autodiscovery (ainda): autodiscovery via `pkgutil`
(o mesmo padrão de `application/workers/registry.py::_autodiscover_workers`)
é a evolução natural apontada na revisão do plano — mas com só 4 agentes
reais existindo até agora, introduzir esse mecanismo agora seria
especulativo. Este módulo é o único lugar que precisa mudar quando isso for
feito (troca o corpo da função, mantém a assinatura `register_all_agents()`).

Sprint 2 (Fase 4): depois de registrar cada agente no `AgentRegistry`
(em memória, por processo), faz upsert best-effort no catálogo Postgres
(`agentes_catalogo`) via `AgentCatalogRepository.upsert_from_code` — nunca
derruba o registro/startup se o Postgres falhar (mesma filosofia defensiva
de `src/main.py`).
"""
from __future__ import annotations

import logging

from src.agents.registry import registry

logger = logging.getLogger(__name__)

_REGISTERED = False


async def register_all_agents() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    from src.agents.academic_knowledge.service import AcademicKnowledgeAgent
    from src.agents.sigaa.service import SigaaAgent
    from src.agents.conversation.registration import ConversationAgent
    from src.agents.tickets.service import TicketAgent

    registry.register(AcademicKnowledgeAgent())
    registry.register(SigaaAgent())
    registry.register(ConversationAgent())
    registry.register(TicketAgent())

    _REGISTERED = True
    logger.info("✅ [AGENT REGISTRY] Agentes registrados: %s", [a.name for a in registry.all()])

    await _upsert_catalogo_best_effort()


async def _upsert_catalogo_best_effort() -> None:
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository

        async with AsyncSessionLocal() as session:
            repo = AgentCatalogRepository(session)
            for agente in registry.all():
                await repo.upsert_from_code(
                    nome=agente.name,
                    descricao_padrao=agente.description,
                    permissions=list(agente.permissions),
                )
            await session.commit()
        logger.info("✅ [AGENT CATALOG] Upsert de %d agentes no Postgres concluído.", len(registry.all()))
    except Exception as exc:
        logger.warning("⚠️  [AGENT CATALOG] Falha no upsert do catálogo Postgres (não bloqueia o startup): %s", exc)
