"""
src/capabilities/agent_tools.py — vínculo agente↔capability (Plano A / Fase 5)
================================================================================
Lê/escreve `agente_tools` (migration 012). Mesma filosofia de `agent_config.py`
(toggle de agente): o código decide QUAIS capabilities um agente tem
(`agente.tools` na classe → `upsert_binding_from_code` no bootstrap); o admin
só liga/desliga (`set_habilitado` via /hub/capabilities).

Degrada sem exceção — falha de Postgres → lista vazia (nenhuma capability),
nunca derruba o fluxo do agente.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.database.models import AgenteTool

logger = logging.getLogger(__name__)


async def upsert_binding_from_code(session, agente: str, tools: list[str]) -> None:
    """Garante que cada `tool` de `tools` tem uma linha `(agente, tool)`.
    NÃO mexe em `habilitado` de linhas que já existem — preserva o toggle
    do admin. NÃO remove bindings que sumiram do código (decisão explícita:
    remover é ação do admin)."""
    for tool in tools:
        stmt = pg_insert(AgenteTool).values(agente=agente, tool=tool)
        stmt = stmt.on_conflict_do_nothing(index_elements=["tenant_id", "agente", "tool"])
        await session.execute(stmt)
    await session.flush()


async def tools_habilitados(session, agente: str) -> list[str]:
    """Capabilities atualmente ligadas para o agente."""
    try:
        result = await session.execute(
            select(AgenteTool.tool).where(
                AgenteTool.agente == agente,
                AgenteTool.tenant_id.is_(None),
                AgenteTool.habilitado.is_(True),
            ).order_by(AgenteTool.tool)
        )
        return [r for r in result.scalars().all()]
    except Exception as exc:
        logger.warning("⚠️  [AGENT TOOLS] Falha ao ler bindings de '%s': %s", agente, exc)
        return []


async def listar(session) -> list[dict]:
    result = await session.execute(
        select(AgenteTool).where(AgenteTool.tenant_id.is_(None))
        .order_by(AgenteTool.agente, AgenteTool.tool)
    )
    return [
        {
            "agente": r.agente, "tool": r.tool, "habilitado": r.habilitado,
            "atualizado_em": r.atualizado_em, "atualizado_por": r.atualizado_por,
        }
        for r in result.scalars().all()
    ]


async def set_habilitado(session, agente: str, tool: str, habilitado: bool, admin: str | None = None) -> bool:
    """Liga/desliga um binding. Retorna False se o binding não existe."""
    res = await session.execute(
        update(AgenteTool)
        .where(AgenteTool.agente == agente, AgenteTool.tool == tool, AgenteTool.tenant_id.is_(None))
        .values(habilitado=habilitado, atualizado_por=admin, atualizado_em=datetime.now(timezone.utc))
    )
    await session.flush()
    return res.rowcount > 0
