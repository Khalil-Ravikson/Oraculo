"""
src/infrastructure/repositories/agent_catalog_repository.py
===============================================================
Sprint 2 (Fase 4) — repositório do catálogo administrável de agentes
(`agentes_catalogo`, migration 005). `descricao`/`ativo` são o estado
administrável, editado via hub, nunca sobrescrito por `upsert_from_code`.
(A coluna `permissions` foi removida na Fase 5 — ver `capabilities/agent_tools.py`.)
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

    async def upsert_from_code(self, nome: str, descricao_padrao: str) -> None:
        """Garante que a linha do agente existe. NÃO sobrescreve
        `descricao`/`ativo` se a linha já existir — preserva edição
        administrativa feita via hub. (A coluna `permissions` foi removida na
        Fase 5 — o vínculo agente↔capability é `agente_tools`.)"""
        stmt = pg_insert(AgenteCatalogo).values(nome=nome, descricao=descricao_padrao)
        stmt = stmt.on_conflict_do_nothing(index_elements=["nome"])
        await self._session.execute(stmt)
        await self._session.flush()

    async def listar(self) -> list[dict]:
        result = await self._session.execute(select(AgenteCatalogo))
        return [
            {
                "nome": row.nome,
                "descricao": row.descricao,
                "ativo": row.ativo,
                "llm_provider": row.llm_provider,
                "llm_model": row.llm_model,
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
            "ativo": row.ativo,
            "llm_provider": row.llm_provider,
            "llm_model": row.llm_model,
            "atualizado_em": row.atualizado_em,
            "atualizado_por": row.atualizado_por,
        }

    async def set_llm_override(
        self, nome: str, llm_provider: str | None, llm_model: str | None, admin: str | None = None,
    ) -> None:
        """Define (ou limpa, passando None) o provider/modelo LLM específico
        deste agente. NULL nos dois campos volta a herdar o global."""
        await self._session.execute(
            update(AgenteCatalogo)
            .where(AgenteCatalogo.nome == nome)
            .values(
                llm_provider=llm_provider or None,
                llm_model=llm_model or None,
                atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()

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
