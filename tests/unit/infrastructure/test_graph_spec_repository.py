"""
`GraphSpecRepository` (migration 024, ADR 0008 Fase 5) contra Postgres real
(o CI provê postgres:16 na 5433 e roda `alembic upgrade head`). Cobre:
optimistic lock (conflito de versão), histórico, revert.

Pula se o Postgres de teste não responder.
"""
import pytest

from src.application.orchestration.loader import default_spec
from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.repositories.graph_spec_repository import (
    ConflitoDeVersao,
    GraphSpecRepository,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def db_limpo():
    import psycopg2
    from src.infrastructure.settings import settings

    url = settings.DATABASE_URL.replace("+asyncpg", "")
    try:
        conn = psycopg2.connect(url)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres de teste indisponível: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE graph_spec, graph_spec_historico RESTART IDENTITY")
    conn.close()
    yield


def _spec_dict():
    return default_spec().model_dump()


async def test_primeira_gravacao_cria_versao_1(db_limpo):
    async with AsyncSessionLocal() as s:
        out = await GraphSpecRepository(s).salvar(_spec_dict(), versao_esperada=0, atualizado_por="admin")
        await s.commit()
    assert out["versao"] == 1

    async with AsyncSessionLocal() as s:
        atual = await GraphSpecRepository(s).obter()
        hist = await GraphSpecRepository(s).historico()
    assert atual["versao"] == 1
    assert [h["versao"] for h in hist] == [1]


async def test_segunda_gravacao_incrementa_versao(db_limpo):
    async with AsyncSessionLocal() as s:
        await GraphSpecRepository(s).salvar(_spec_dict(), versao_esperada=0, atualizado_por="a")
        await s.commit()

    d = _spec_dict()
    d["version"] = 2
    async with AsyncSessionLocal() as s:
        out = await GraphSpecRepository(s).salvar(d, versao_esperada=1, atualizado_por="b")
        await s.commit()
    assert out["versao"] == 2

    async with AsyncSessionLocal() as s:
        hist = await GraphSpecRepository(s).historico()
    assert [h["versao"] for h in hist] == [2, 1]


async def test_versao_obsoleta_recebe_conflito(db_limpo):
    async with AsyncSessionLocal() as s:
        await GraphSpecRepository(s).salvar(_spec_dict(), versao_esperada=0, atualizado_por="a")
        await s.commit()

    with pytest.raises(ConflitoDeVersao):
        async with AsyncSessionLocal() as s:
            await GraphSpecRepository(s).salvar(_spec_dict(), versao_esperada=0, atualizado_por="b")
            await s.commit()


async def test_reverter_restaura_snapshot(db_limpo):
    async with AsyncSessionLocal() as s:
        await GraphSpecRepository(s).salvar(_spec_dict(), versao_esperada=0, atualizado_por="a")
        await s.commit()

    d = _spec_dict()
    d["nodes"].append({"id": "faq", "type": "rag", "config": {}})
    d["edges"].append({"source": "classify", "when": "by_state_route", "route_value": "faq", "target": "faq"})
    d["edges"].append({"source": "faq", "target": "__end__"})
    async with AsyncSessionLocal() as s:
        await GraphSpecRepository(s).salvar(d, versao_esperada=1, atualizado_por="b")
        await s.commit()

    async with AsyncSessionLocal() as s:
        snap_v1 = await GraphSpecRepository(s).snapshot_da_versao(1)
        out = await GraphSpecRepository(s).salvar(snap_v1, versao_esperada=2, atualizado_por="c (revert)")
        await s.commit()
    assert out["versao"] == 3
    assert not any(n["id"] == "faq" for n in out["spec"]["nodes"])
