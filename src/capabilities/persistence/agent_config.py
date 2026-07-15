"""
src/capabilities/persistence/agent_config.py
================================================
Primeira convenção de config-por-agente no Redis, criada para o painel
`/hub/agents`: liga/desliga por agente, checado em `BaseAgent.can_execute()`.

Chave: `admin:agent:{nome}:enabled` — string "0" desativa; qualquer outro
valor (inclusive chave ausente) é tratado como ativo, para que agentes
recém-registrados não fiquem desligados por padrão.

Sprint 2 (Fase 5): `set_agent_enabled` passa a gravar também no catálogo
Postgres (`agentes_catalogo`, via `AgentCatalogRepository.set_ativo`),
best-effort — falha no Postgres não impede o toggle no Redis. A LEITURA
(`is_agent_enabled`) continua 100% Redis nesta fase; o flip para
Postgres-first é a Fase 6.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _chave(nome: str) -> str:
    return f"admin:agent:{nome}:enabled"


def is_agent_enabled(redis, nome: str) -> bool:
    """Ativo por padrão — só é desativado se a chave existir com valor '0'."""
    try:
        raw = redis.get(_chave(nome))
    except Exception:
        return True
    if raw is None:
        return True
    valor = raw if isinstance(raw, str) else raw.decode()
    return valor != "0"


async def set_agent_enabled(redis, nome: str, enabled: bool, admin: str | None = None) -> None:
    redis.set(_chave(nome), "1" if enabled else "0")
    await _set_ativo_catalogo_best_effort(nome, enabled, admin)


async def _set_ativo_catalogo_best_effort(nome: str, enabled: bool, admin: str | None) -> None:
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository

        async with AsyncSessionLocal() as session:
            repo = AgentCatalogRepository(session)
            await repo.set_ativo(nome, enabled, admin)
            await session.commit()
    except Exception as exc:
        logger.warning(
            "⚠️  [AGENT CATALOG] Falha ao gravar toggle de '%s' no Postgres (Redis já gravado): %s",
            nome, exc,
        )


def status_de_todos(redis, nomes: list[str]) -> dict[str, bool]:
    return {nome: is_agent_enabled(redis, nome) for nome in nomes}
