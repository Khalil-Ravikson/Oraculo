"""
src/infrastructure/repositories/dynamic_config_repository.py
================================================================================
Repositório das tabelas `config_dinamica` / `config_dinamica_historico`
(migration 009, Plano A / Fase 1). Mesmo espírito de `LlmPricingRepository`:
Postgres é a fonte de verdade, os métodos NÃO commitam (o caller — endpoint
admin — commita), auditoria de `atualizado_por`/`atualizado_em` na linha.

O que este repositório adiciona em relação aos outros (§N):

  * Controle de concorrência otimista — `upsert` recebe a `versao` que o admin
    tinha na tela e o UPDATE inclui `WHERE versao = :versao_esperada`. Se
    afetar 0 linhas, outra escrita aconteceu no meio: `ConflitoDeVersao`
    (o endpoint devolve HTTP 409, o Hub manda recarregar).

  * Histórico append-only — toda escrita bem-sucedida insere uma linha em
    `config_dinamica_historico` com `valor_antigo`/`valor_novo`.

`tenant_id` é sempre NULL (§M) — o WHERE fixa `tenant_id IS NULL`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import ConfigDinamica, ConfigDinamicaHistorico
from src.infrastructure.repositories._optimistic import ConflitoDeVersao

__all__ = ["DynamicConfigRepository", "ConflitoDeVersao"]


class DynamicConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ─── Leitura ─────────────────────────────────────────────────────────────

    async def listar(self) -> list[dict]:
        result = await self._session.execute(
            select(ConfigDinamica)
            .where(ConfigDinamica.tenant_id.is_(None))
            .order_by(ConfigDinamica.chave)
        )
        return [self._linha(row) for row in result.scalars().all()]

    async def obter(self, chave: str) -> dict | None:
        result = await self._session.execute(
            select(ConfigDinamica).where(
                ConfigDinamica.chave == chave,
                ConfigDinamica.tenant_id.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        return self._linha(row) if row is not None else None

    async def historico(self, chave: str) -> list[dict]:
        result = await self._session.execute(
            select(ConfigDinamicaHistorico)
            .where(ConfigDinamicaHistorico.chave == chave)
            .order_by(ConfigDinamicaHistorico.versao.desc(), ConfigDinamicaHistorico.id.desc())
        )
        return [
            {
                "versao": row.versao,
                "valor_antigo": row.valor_antigo,
                "valor_novo": row.valor_novo,
                "atualizado_por": row.atualizado_por,
                "atualizado_em": row.atualizado_em,
            }
            for row in result.scalars().all()
        ]

    async def valor_na_versao(self, chave: str, versao: int) -> str | None:
        """`valor_novo` que a chave assumiu na versão `versao` — usado pelo
        botão 'reverter' do Hub."""
        result = await self._session.execute(
            select(ConfigDinamicaHistorico.valor_novo)
            .where(
                ConfigDinamicaHistorico.chave == chave,
                ConfigDinamicaHistorico.versao == versao,
            )
            .order_by(ConfigDinamicaHistorico.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    # ─── Escrita (optimistic lock + histórico) ───────────────────────────────

    async def upsert(
        self,
        chave: str,
        valor: str,
        tipo: str,
        *,
        versao_esperada: int,
        atualizado_por: str | None = None,
    ) -> int:
        """Grava `valor` para `chave` e devolve a nova `versao`.
        Levanta `ConflitoDeVersao` se `versao_esperada` não bater com o banco.
        Não commita — o caller é responsável pelo `session.commit()`.
        """
        agora = datetime.now(timezone.utc)
        atual = await self.obter(chave)

        if atual is None:
            # Linha ainda não existe (seed não rodou / chave nova). Cria como v1.
            try:
                await self._session.execute(
                    pg_insert(ConfigDinamica).values(
                        chave=chave, valor=valor, tipo=tipo, versao=1,
                        atualizado_por=atualizado_por, atualizado_em=agora,
                    )
                )
                await self._session.flush()
            except IntegrityError:
                # Corrida: outra request inseriu a mesma chave primeiro.
                raise ConflitoDeVersao(chave, versao_esperada, None)
            await self._inserir_historico(chave, None, valor, 1, atualizado_por, agora)
            return 1

        if versao_esperada != atual["versao"]:
            raise ConflitoDeVersao(chave, versao_esperada, atual["versao"])

        nova_versao = atual["versao"] + 1
        res = await self._session.execute(
            update(ConfigDinamica)
            .where(
                ConfigDinamica.chave == chave,
                ConfigDinamica.tenant_id.is_(None),
                ConfigDinamica.versao == versao_esperada,
            )
            .values(
                valor=valor, tipo=tipo, versao=nova_versao,
                atualizado_por=atualizado_por, atualizado_em=agora,
            )
        )
        if res.rowcount == 0:
            # Outra escrita commitou entre o obter() e o update().
            recheck = await self.obter(chave)
            raise ConflitoDeVersao(chave, versao_esperada, recheck["versao"] if recheck else None)

        await self._inserir_historico(chave, atual["valor"], valor, nova_versao, atualizado_por, agora)
        return nova_versao

    # ─── Internos ────────────────────────────────────────────────────────────

    async def _inserir_historico(
        self,
        chave: str,
        valor_antigo: str | None,
        valor_novo: str,
        versao: int,
        atualizado_por: str | None,
        agora: datetime,
    ) -> None:
        await self._session.execute(
            pg_insert(ConfigDinamicaHistorico).values(
                chave=chave,
                valor_antigo=valor_antigo,
                valor_novo=valor_novo,
                versao=versao,
                atualizado_por=atualizado_por,
                atualizado_em=agora,
            )
        )
        await self._session.flush()

    @staticmethod
    def _linha(row: ConfigDinamica) -> dict:
        return {
            "chave": row.chave,
            "valor": row.valor,
            "tipo": row.tipo,
            "versao": row.versao,
            "atualizado_em": row.atualizado_em,
            "atualizado_por": row.atualizado_por,
        }
