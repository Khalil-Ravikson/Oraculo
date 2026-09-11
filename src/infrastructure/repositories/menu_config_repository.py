"""
src/infrastructure/repositories/menu_config_repository.py
========================================================
Repositório de `menu_config` / `menu_config_historico` (migration 025, item
C2.5). Gêmeo de `GraphSpecRepository` — mesma disciplina, pelos mesmos
motivos:

  * optimistic lock — `salvar` recebe a `versao` que o admin tinha na tela;
    `UPDATE ... WHERE versao = :esperada`; 0 linhas → `ConflitoDeVersao`.
    Duas pessoas editando o menu ao mesmo tempo é cenário real num setor com
    mais de um servidor no painel.
  * histórico append-only — cada escrita guarda o menu inteiro;
    `reverter(versao)` restaura um snapshot como escrita nova, nunca como
    apagamento.

Uma linha só (`tenant_id` NULL). Métodos NÃO commitam — o endpoint commita.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import MenuConfig, MenuConfigHistorico
from src.infrastructure.repositories._optimistic import ConflitoDeVersao

__all__ = ["MenuConfigRepository", "ConflitoDeVersao"]


class MenuConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def obter(self) -> dict | None:
        """`{config, versao, atualizado_em, atualizado_por}`, ou None se o menu
        nunca foi editado pelo painel — nesse caso vale o `default.json`."""
        row = (await self._session.execute(
            select(MenuConfig).where(MenuConfig.tenant_id.is_(None))
        )).scalar_one_or_none()
        if row is None:
            return None
        return {
            "config": row.config, "versao": row.versao,
            "atualizado_em": row.atualizado_em, "atualizado_por": row.atualizado_por,
        }

    async def historico(self) -> list[dict]:
        rows = (await self._session.execute(
            select(MenuConfigHistorico)
            .where(MenuConfigHistorico.tenant_id.is_(None))
            .order_by(MenuConfigHistorico.versao.desc(), MenuConfigHistorico.id.desc())
        )).scalars().all()
        return [
            {"versao": r.versao, "snapshot": r.snapshot,
             "atualizado_por": r.atualizado_por, "atualizado_em": r.atualizado_em}
            for r in rows
        ]

    async def snapshot_da_versao(self, versao: int) -> dict | None:
        return (await self._session.execute(
            select(MenuConfigHistorico.snapshot)
            .where(MenuConfigHistorico.tenant_id.is_(None), MenuConfigHistorico.versao == versao)
            .order_by(MenuConfigHistorico.id.desc()).limit(1)
        )).scalars().first()

    async def salvar(
        self, config: dict, *, versao_esperada: int, atualizado_por: str | None = None,
    ) -> dict:
        """Grava o menu (já validado por `menu.spec.validate_menu()` — este
        repositório NÃO valida conteúdo, só concorrência). Devolve
        `{config, versao}`. Levanta `ConflitoDeVersao`."""
        agora = datetime.now(timezone.utc)
        atual = await self.obter()

        if atual is None:
            await self._session.execute(pg_insert(MenuConfig).values(
                config=config, versao=1, tenant_id=None,
                atualizado_por=atualizado_por, atualizado_em=agora,
            ))
            await self._historico(1, config, atualizado_por, agora)
            await self._session.flush()
            return {"config": config, "versao": 1}

        if versao_esperada != atual["versao"]:
            raise ConflitoDeVersao("menu_config", versao_esperada, atual["versao"])

        nova = atual["versao"] + 1
        res = await self._session.execute(
            update(MenuConfig)
            .where(MenuConfig.tenant_id.is_(None), MenuConfig.versao == versao_esperada)
            .values(config=config, versao=nova, atualizado_por=atualizado_por, atualizado_em=agora)
        )
        if res.rowcount == 0:
            recheck = await self.obter()
            raise ConflitoDeVersao("menu_config", versao_esperada, recheck["versao"] if recheck else None)
        await self._historico(nova, config, atualizado_por, agora)
        await self._session.flush()
        return {"config": config, "versao": nova}

    async def _historico(
        self, versao: int, snapshot: dict, atualizado_por: str | None, agora: datetime,
    ) -> None:
        await self._session.execute(pg_insert(MenuConfigHistorico).values(
            versao=versao, snapshot=snapshot, tenant_id=None,
            atualizado_por=atualizado_por, atualizado_em=agora,
        ))

    async def espelhar_redis(self, config: dict) -> None:
        """Chamado pelo endpoint depois de `salvar` + commit.

        O menu é lido no caminho quente de TODA mensagem, então ele lê do
        Redis antes do Postgres (ver `application/menu/loader.py`). Sem este
        espelho, editar um texto no painel não teria efeito até o cache
        expirar.

        Falha silenciosa de propósito: se o Redis não aceitar a escrita, o
        `loader` cai no Postgres, que já tem o valor novo. Mais lento, correto
        do mesmo jeito."""
        try:
            import json

            from src.application.menu.loader import MENU_REDIS_KEY
            from src.infrastructure.redis_client import get_redis_text

            get_redis_text().set(MENU_REDIS_KEY, json.dumps(config, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
