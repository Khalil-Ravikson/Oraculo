"""
src/infrastructure/repositories/route_registry_repository.py
================================================================================
Repositório de `route_registry` / `route_registry_historico` (migration 010,
Plano A / Fase 2). Mesma mecânica de `DynamicConfigRepository`:

  * optimistic lock (§N) — `upsert` recebe a `versao` que o admin tinha na
    tela; `UPDATE ... WHERE versao = :versao_esperada`; 0 linhas →
    `ConflitoDeVersao` (HTTP 409).
  * histórico append-only — cada escrita insere um snapshot da linha inteira
    (o botão "reverter" do Hub restaura o snapshot).

`tenant_id` sempre NULL (§M). Métodos NÃO commitam — o endpoint commita.
`upsert` devolve o `RouteConfig` persistido (o endpoint reaproveita p/ espelhar
no Redis, sem reabrir sessão).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import RouteRegistry, RouteRegistryHistorico
from src.infrastructure.repositories._optimistic import ConflitoDeVersao
from src.infrastructure.route_registry import RouteConfig, merge_default, to_dict

__all__ = ["RouteRegistryRepository", "ConflitoDeVersao"]


def _to_config(row: RouteRegistry) -> RouteConfig:
    return RouteConfig(
        rota=row.rota, entrypoint_node=row.entrypoint_node, owner=row.owner,
        agente=row.agente, cacheavel=row.cacheavel, permite_detour=row.permite_detour,
        doc_type=row.doc_type, k=row.k,
        versao=row.versao,
    )


class RouteRegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─── Leitura ─────────────────────────────────────────────────────────────

    async def listar(self) -> list[RouteConfig]:
        result = await self._session.execute(
            select(RouteRegistry)
            .where(RouteRegistry.tenant_id.is_(None))
            .order_by(RouteRegistry.rota)
        )
        return [_to_config(r) for r in result.scalars().all()]

    async def obter(self, rota: str) -> RouteConfig | None:
        result = await self._session.execute(
            select(RouteRegistry).where(
                RouteRegistry.rota == rota, RouteRegistry.tenant_id.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        return _to_config(row) if row is not None else None

    async def historico(self, rota: str) -> list[dict]:
        result = await self._session.execute(
            select(RouteRegistryHistorico)
            .where(RouteRegistryHistorico.rota == rota)
            .order_by(RouteRegistryHistorico.versao.desc(), RouteRegistryHistorico.id.desc())
        )
        return [
            {
                "versao": r.versao,
                "snapshot": r.snapshot,
                "atualizado_por": r.atualizado_por,
                "atualizado_em": r.atualizado_em,
            }
            for r in result.scalars().all()
        ]

    async def snapshot_da_versao(self, rota: str, versao: int) -> dict | None:
        result = await self._session.execute(
            select(RouteRegistryHistorico.snapshot)
            .where(
                RouteRegistryHistorico.rota == rota,
                RouteRegistryHistorico.versao == versao,
            )
            .order_by(RouteRegistryHistorico.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    # ─── Escrita (optimistic lock + histórico) ───────────────────────────────

    async def upsert(
        self,
        rota: str,
        campos: dict,
        *,
        versao_esperada: int,
        atualizado_por: str | None = None,
    ) -> RouteConfig:
        """Aplica `campos` (já validados por `route_registry.validar_campos`)
        sobre a rota e devolve o `RouteConfig` persistido. Levanta
        `ConflitoDeVersao`."""
        agora = datetime.now(timezone.utc)
        atual = await self.obter(rota)

        if atual is None:
            novo = merge_default(rota, campos)   # já vem com versao=1
            valores = {k: v for k, v in to_dict(novo).items() if k != "versao"}
            valores.update(versao=1, atualizado_por=atualizado_por, atualizado_em=agora)
            try:
                await self._session.execute(pg_insert(RouteRegistry).values(**valores))
                await self._session.flush()
            except IntegrityError:
                raise ConflitoDeVersao(rota, versao_esperada, None)
            await self._historico(rota, 1, to_dict(novo), atualizado_por, agora)
            return novo

        if versao_esperada != atual.versao:
            raise ConflitoDeVersao(rota, versao_esperada, atual.versao)

        nova_versao = atual.versao + 1
        set_values = dict(campos)
        set_values.update(versao=nova_versao, atualizado_por=atualizado_por, atualizado_em=agora)

        res = await self._session.execute(
            update(RouteRegistry)
            .where(
                RouteRegistry.rota == rota,
                RouteRegistry.tenant_id.is_(None),
                RouteRegistry.versao == versao_esperada,
            )
            .values(**set_values)
        )
        if res.rowcount == 0:
            recheck = await self.obter(rota)
            raise ConflitoDeVersao(rota, versao_esperada, recheck.versao if recheck else None)

        persistido = await self.obter(rota)
        await self._historico(rota, nova_versao, to_dict(persistido), atualizado_por, agora)
        return persistido

    async def remover(self, rota: str) -> bool:
        """Apaga a linha e o histórico. Só faz sentido para rota personalizada
        (a checagem `route_registry.pode_apagar` é do endpoint)."""
        res = await self._session.execute(
            delete(RouteRegistry).where(
                RouteRegistry.rota == rota, RouteRegistry.tenant_id.is_(None),
            )
        )
        await self._session.execute(
            delete(RouteRegistryHistorico).where(RouteRegistryHistorico.rota == rota)
        )
        await self._session.flush()
        return res.rowcount > 0

    async def _historico(
        self, rota: str, versao: int, snapshot: dict, atualizado_por: str | None, agora: datetime,
    ) -> None:
        await self._session.execute(
            pg_insert(RouteRegistryHistorico).values(
                rota=rota, versao=versao, snapshot=snapshot,
                atualizado_por=atualizado_por, atualizado_em=agora,
            )
        )
        await self._session.flush()
