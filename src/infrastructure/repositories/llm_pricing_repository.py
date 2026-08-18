"""
src/infrastructure/repositories/llm_pricing_repository.py
===============================================================
Repositório da tabela `llm_pricing` (migration 008) — mesmo espírito de
`AgentCatalogRepository`: Postgres é a fonte de verdade, editável via hub.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import LlmPricing


class LlmPricingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def listar(self) -> list[dict]:
        result = await self._session.execute(
            select(LlmPricing).order_by(LlmPricing.provider, LlmPricing.modelo)
        )
        return [
            {
                "provider": row.provider,
                "modelo": row.modelo,
                "input_por_1m": float(row.input_por_1m),
                "output_por_1m": float(row.output_por_1m),
                "cache_por_1m": float(row.cache_por_1m) if row.cache_por_1m is not None else None,
                "atualizado_em": row.atualizado_em,
                "atualizado_por": row.atualizado_por,
            }
            for row in result.scalars().all()
        ]

    async def upsert(
        self,
        provider: str,
        modelo: str,
        input_por_1m: float,
        output_por_1m: float,
        cache_por_1m: float | None,
        admin: str | None = None,
    ) -> None:
        stmt = pg_insert(LlmPricing).values(
            provider=provider,
            modelo=modelo,
            input_por_1m=input_por_1m,
            output_por_1m=output_por_1m,
            cache_por_1m=cache_por_1m,
            atualizado_por=admin,
            atualizado_em=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "modelo"],
            set_={
                "input_por_1m": stmt.excluded.input_por_1m,
                "output_por_1m": stmt.excluded.output_por_1m,
                "cache_por_1m": stmt.excluded.cache_por_1m,
                "atualizado_por": stmt.excluded.atualizado_por,
                "atualizado_em": stmt.excluded.atualizado_em,
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()
