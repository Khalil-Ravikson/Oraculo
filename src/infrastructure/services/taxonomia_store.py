"""
src/infrastructure/services/taxonomia_store.py — taxonomia da wiki CTIC
========================================================================
Postgres (`wiki_taxonomia`, migration 028). Substitui o dict hardcoded
`hierarchy.KNOWN_SYSTEM_HUBS` — mesmo papel, editável pelo painel em vez de
por deploy.

`listar_hubs_dict()` é o ponto de integração com o scraper: carrega a
tabela inteira UMA VEZ por rodada de ingestão (não por página), no mesmo
formato que `KNOWN_SYSTEM_HUBS` usava (`{page_id: (sistema, modulo)}`), para
`hierarchy.resolver_taxonomia()` continuar sendo uma função síncrona pura.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.models import WikiTaxonomia

logger = logging.getLogger(__name__)


class PageIdDuplicadoError(ValueError):
    pass


class ConfigInvalidaError(ValueError):
    pass


async def listar(session) -> list[dict]:
    rows = (await session.execute(
        select(WikiTaxonomia).where(WikiTaxonomia.tenant_id.is_(None)).order_by(WikiTaxonomia.page_id)
    )).scalars().all()
    return [_row_dict(r) for r in rows]


async def listar_hubs_dict(session) -> dict[str, tuple[str, str]]:
    """Formato consumido por `hierarchy.resolver_taxonomia()`."""
    linhas = await listar(session)
    return {r["page_id"]: (r["sistema"], r["modulo"]) for r in linhas}


async def criar(session, page_id: str, sistema: str, modulo: str, admin: str | None = None) -> dict:
    page_id = (page_id or "").strip()
    sistema = (sistema or "").strip()
    modulo = (modulo or "").strip()
    if not page_id or not sistema or not modulo:
        raise ConfigInvalidaError("Informe page_id, sistema e módulo.")

    registro = WikiTaxonomia(page_id=page_id, sistema=sistema, modulo=modulo, atualizado_por=admin)
    session.add(registro)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise PageIdDuplicadoError(f"'{page_id}' já está classificado.") from exc
    return _row_dict(registro)


async def atualizar(session, taxonomia_id: int, sistema: str, modulo: str, admin: str | None = None) -> bool:
    sistema = (sistema or "").strip()
    modulo = (modulo or "").strip()
    if not sistema or not modulo:
        raise ConfigInvalidaError("Informe sistema e módulo.")
    res = await session.execute(
        update(WikiTaxonomia).where(WikiTaxonomia.id == taxonomia_id, WikiTaxonomia.tenant_id.is_(None))
        .values(sistema=sistema, modulo=modulo, atualizado_por=admin, atualizado_em=datetime.now(timezone.utc))
    )
    await session.flush()
    return res.rowcount > 0


async def remover(session, taxonomia_id: int) -> bool:
    res = await session.execute(
        delete(WikiTaxonomia).where(WikiTaxonomia.id == taxonomia_id, WikiTaxonomia.tenant_id.is_(None))
    )
    await session.flush()
    return res.rowcount > 0


def _row_dict(r: WikiTaxonomia) -> dict:
    return {
        "id": r.id, "page_id": r.page_id, "sistema": r.sistema, "modulo": r.modulo,
        "atualizado_em": r.atualizado_em.isoformat() if r.atualizado_em else None,
        "atualizado_por": r.atualizado_por,
    }
