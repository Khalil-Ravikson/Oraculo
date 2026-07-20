"""
src/infrastructure/repositories/agent_catalog_repository.py
===============================================================
Sprint 2 (Fase 4) — repositório do catálogo administrável de agentes
(`agentes_catalogo`, migration 005). `permissions` é sempre espelho
somente-leitura do código via `upsert_from_code`; `descricao`/`ativo` são o
estado administrável, editado via hub, nunca sobrescrito pelo upsert.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import AgenteCatalogo


class AgentCatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_from_code(self, nome: str, descricao_padrao: str, permissions: list[str]) -> None:
        """Garante que a linha do agente existe e que `permissions` reflete o
        código atual. NÃO sobrescreve `descricao`/`ativo` se a linha já
        existir — preserva edição administrativa feita via hub."""
        stmt = pg_insert(AgenteCatalogo).values(
            nome=nome,
            descricao=descricao_padrao,
            permissions=permissions,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["nome"],
            set_={"permissions": stmt.excluded.permissions},
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def listar(self) -> list[dict]:
        result = await self._session.execute(select(AgenteCatalogo))
        return [
            {
                "nome": row.nome,
                "descricao": row.descricao,
                "permissions": row.permissions,
                "ativo": row.ativo,
                "criado_em": row.criado_em,
                "atualizado_em": row.atualizado_em,
                "atualizado_por": row.atualizado_por,
            }
            for row in result.scalars().all()
        ]

    async def obter(self, nome: str) -> dict | None:
        result = await self._session.execute(
            select(AgenteCatalogo).where(AgenteCatalogo.nome == nome)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return {
            "nome": row.nome,
            "descricao": row.descricao,
            "permissions": row.permissions,
            "ativo": row.ativo,
            "atualizado_em": row.atualizado_em,
            "atualizado_por": row.atualizado_por,
        }

    async def set_ativo(self, nome: str, ativo: bool, admin: str | None = None) -> None:
        await self._session.execute(
            update(AgenteCatalogo)
            .where(AgenteCatalogo.nome == nome)
            .values(ativo=ativo, atualizado_por=admin, atualizado_em=datetime.now(timezone.utc))
        )
        await self._session.flush()

    async def atualizar_descricao(self, nome: str, nova_descricao: str, admin: str | None = None) -> None:
        await self._session.execute(
            update(AgenteCatalogo)
            .where(AgenteCatalogo.nome == nome)
            .values(descricao=nova_descricao, atualizado_por=admin, atualizado_em=datetime.now(timezone.utc))
        )
        await self._session.flush()
