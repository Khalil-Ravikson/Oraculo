"""
src/infrastructure/repositories/graph_spec_repository.py
=======================================================
Repositório de `graph_spec` / `graph_spec_historico` (migration 024, ADR 0008
Fase 5). Mesma disciplina de `RouteRegistryRepository`:

  * optimistic lock (§N) — `salvar` recebe a `versao` que o admin tinha na
    tela; `UPDATE ... WHERE versao = :esperada`; 0 linhas → `ConflitoDeVersao`.
  * histórico append-only — cada escrita insere o snapshot da spec inteira;
    `reverter(versao)` restaura um snapshot como escrita nova.

Uma linha só (`tenant_id` NULL). Métodos NÃO commitam — o endpoint commita.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import GraphSpec, GraphSpecHistorico
from src.infrastructure.repositories._optimistic import ConflitoDeVersao

__all__ = ["GraphSpecRepository", "ConflitoDeVersao"]


class GraphSpecRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def obter(self) -> dict | None:
        """`{spec, versao, atualizado_em, atualizado_por}` ou None se nunca gravada."""
        row = (await self._session.execute(
            select(GraphSpec).where(GraphSpec.tenant_id.is_(None))
        )).scalar_one_or_none()
        if row is None:
            return None
        return {
            "spec": row.spec, "versao": row.versao,
            "atualizado_em": row.atualizado_em, "atualizado_por": row.atualizado_por,
        }

    async def historico(self) -> list[dict]:
        rows = (await self._session.execute(
            select(GraphSpecHistorico)
            .where(GraphSpecHistorico.tenant_id.is_(None))
            .order_by(GraphSpecHistorico.versao.desc(), GraphSpecHistorico.id.desc())
        )).scalars().all()
        return [
            {"versao": r.versao, "snapshot": r.snapshot,
             "atualizado_por": r.atualizado_por, "atualizado_em": r.atualizado_em}
            for r in rows
        ]

    async def snapshot_da_versao(self, versao: int) -> dict | None:
        return (await self._session.execute(
            select(GraphSpecHistorico.snapshot)
            .where(GraphSpecHistorico.tenant_id.is_(None), GraphSpecHistorico.versao == versao)
            .order_by(GraphSpecHistorico.id.desc()).limit(1)
        )).scalars().first()

    async def salvar(
        self, spec: dict, *, versao_esperada: int, atualizado_por: str | None = None,
    ) -> dict:
        """Grava a spec (já validada por `spec.validate_topology()`). Devolve
        `{spec, versao}` persistido. Levanta `ConflitoDeVersao`."""
        agora = datetime.now(timezone.utc)
        atual = await self.obter()

        if atual is None:
            await self._session.execute(pg_insert(GraphSpec).values(
                spec=spec, versao=1, tenant_id=None,
                atualizado_por=atualizado_por, atualizado_em=agora,
            ))
            await self._historico(1, spec, atualizado_por, agora)
            await self._session.flush()
            return {"spec": spec, "versao": 1}

        if versao_esperada != atual["versao"]:
            raise ConflitoDeVersao("graph_spec", versao_esperada, atual["versao"])

        nova = atual["versao"] + 1
        res = await self._session.execute(
            update(GraphSpec)
            .where(GraphSpec.tenant_id.is_(None), GraphSpec.versao == versao_esperada)
            .values(spec=spec, versao=nova, atualizado_por=atualizado_por, atualizado_em=agora)
        )
        if res.rowcount == 0:
            recheck = await self.obter()
            raise ConflitoDeVersao("graph_spec", versao_esperada, recheck["versao"] if recheck else None)
        await self._historico(nova, spec, atualizado_por, agora)
        await self._session.flush()
        return {"spec": spec, "versao": nova}

    async def _historico(
        self, versao: int, snapshot: dict, atualizado_por: str | None, agora: datetime,
    ) -> None:
        await self._session.execute(pg_insert(GraphSpecHistorico).values(
            versao=versao, snapshot=snapshot, tenant_id=None,
            atualizado_por=atualizado_por, atualizado_em=agora,
        ))

    async def espelhar_redis(self, spec: dict) -> None:
        """Chamado pelo endpoint depois de `salvar` + commit — o grafo lê do
        Redis antes do Postgres (ver `orchestration/loader.py`)."""
        try:
            import json
            from src.infrastructure.redis_client import get_redis_text
            get_redis_text().set("graph:spec:ativa", json.dumps(spec, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
