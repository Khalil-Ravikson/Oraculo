"""
src/capabilities/persistence/prompt_config.py
=================================================
Sprint 2 (Fase 8) — módulo central de leitura/escrita de prompts versionados
por agente (`agent_prompts`, migration 006). Substitui a leitura/escrita crua
e duplicada da chave Redis `admin:system_prompt` (espalhada antes em
`synthesis.py`, `admin_api.py`, `cmd_maintenance.py`, `admin_commands.py`).

Ordem de resolução em `obter_prompt_ativo`: Postgres (`active=true` por
agente) → Redis legado (`admin:system_prompt`, período de transição) →
`fallback` (hardcoded, ex. `SYSTEM_SYNTHESIS`). O fallback garante que uma
falha total de Postgres+Redis nunca deixa o agente sem system prompt.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update

from src.infrastructure.database.models import AgentPrompt

logger = logging.getLogger(__name__)


async def obter_prompt_ativo(session, agent_name: str, fallback: str, redis=None) -> str:
    """Postgres `active=true` → Redis legado `admin:system_prompt` → fallback."""
    try:
        result = await session.execute(
            select(AgentPrompt).where(AgentPrompt.agent_name == agent_name, AgentPrompt.active.is_(True))
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row.prompt_text
    except Exception as exc:
        logger.warning("⚠️  [PROMPT CONFIG] Falha ao ler prompt ativo de '%s' no Postgres: %s", agent_name, exc)

    if redis is not None:
        try:
            legado = redis.get("admin:system_prompt")
            if legado:
                return legado if isinstance(legado, str) else legado.decode()
        except Exception:
            pass

    return fallback


async def publicar_novo_prompt(session, agent_name: str, prompt_text: str, created_by: str | None = None) -> AgentPrompt:
    """Desativa a versão ativa anterior (se houver) e insere a nova já ativa,
    na mesma transação. `version` é sempre `max(version) + 1` para o agente."""
    result = await session.execute(
        select(AgentPrompt.version)
        .where(AgentPrompt.agent_name == agent_name)
        .order_by(AgentPrompt.version.desc())
        .limit(1)
    )
    ultima_versao = result.scalar_one_or_none() or 0

    await session.execute(
        update(AgentPrompt)
        .where(AgentPrompt.agent_name == agent_name, AgentPrompt.active.is_(True))
        .values(active=False)
    )

    nova = AgentPrompt(
        agent_name=agent_name,
        prompt_text=prompt_text,
        version=ultima_versao + 1,
        active=True,
        created_by=created_by,
    )
    session.add(nova)
    await session.flush()
    return nova


async def resetar_para_padrao(session, agent_name: str, created_by: str | None = None) -> None:
    """Desativa a versão ativa — nenhuma fica `active=true`, então
    `obter_prompt_ativo` cai no fallback hardcoded."""
    await session.execute(
        update(AgentPrompt)
        .where(AgentPrompt.agent_name == agent_name, AgentPrompt.active.is_(True))
        .values(active=False)
    )
    await session.flush()


async def tem_override_ativo(session, agent_name: str, redis=None) -> bool:
    """True se existe um prompt customizado ativo (Postgres ou, em falha
    de Postgres, o legado `admin:system_prompt` no Redis) — usado só para
    exibir status no painel admin (`GET /system`, `/metrics`)."""
    try:
        result = await session.execute(
            select(AgentPrompt.id).where(AgentPrompt.agent_name == agent_name, AgentPrompt.active.is_(True))
        )
        if result.scalar_one_or_none() is not None:
            return True
        return False
    except Exception:
        if redis is not None:
            try:
                return bool(redis.get("admin:system_prompt"))
            except Exception:
                pass
        return False


async def historico(session, agent_name: str) -> list[dict]:
    result = await session.execute(
        select(AgentPrompt)
        .where(AgentPrompt.agent_name == agent_name)
        .order_by(AgentPrompt.version.desc())
    )
    return [
        {
            "version": row.version,
            "prompt_text": row.prompt_text,
            "active": row.active,
            "created_at": row.created_at,
            "created_by": row.created_by,
        }
        for row in result.scalars().all()
    ]
