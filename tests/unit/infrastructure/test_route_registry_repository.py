"""
Plano A / Fase 2 — `RouteRegistryRepository` + read-repair de `route_registry`,
contra Postgres real (o CI provê postgres:16 na 5433 e roda `alembic upgrade
head`). Cobre os itens de §T que precisam de banco: concorrência (409),
histórico com snapshot da linha, revert, read-repair, degradação.

Pula se o Postgres de teste não responder.
"""
import pytest

from src.infrastructure import route_registry as rr
from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.repositories.route_registry_repository import (
    ConflitoDeVersao,
    RouteRegistryRepository,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _FakeRedisText:
    def __init__(self):
        self._d = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v):
        self._d[k] = str(v)


@pytest.fixture
def db_limpo():
    """Reseta `route_registry` / `_historico` ao seed da migration 010 via
    psycopg2 síncrono. Pula se o Postgres não responder."""
    import psycopg2
    from src.infrastructure.settings import settings

    url = settings.DATABASE_URL.replace("+asyncpg", "")
    try:
        conn = psycopg2.connect(url)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres de teste indisponível: {exc}")

    import json

    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE route_registry, route_registry_historico RESTART IDENTITY")
        for rota, cfg in rr._DEFAULTS.items():
            steps = list(cfg.planner_steps) if cfg.planner_steps is not None else None
            cur.execute(
                "INSERT INTO route_registry "
                "(rota, entrypoint_node, owner, agente, cacheavel, permite_detour, doc_type, k, planner_steps, versao) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
                (rota, cfg.entrypoint_node, cfg.owner, cfg.agente, cfg.cacheavel,
                 cfg.permite_detour, cfg.doc_type, cfg.k, steps),
            )
            cur.execute(
                "INSERT INTO route_registry_historico (rota, versao, snapshot) VALUES (%s, 1, %s)",
                (rota, json.dumps(rr.to_dict(cfg))),
            )
    conn.close()
    yield


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedisText()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: fake)
    return fake


async def test_upsert_incrementa_versao_e_grava_historico(db_limpo):
    async with AsyncSessionLocal() as s:
        cfg = await RouteRegistryRepository(s).upsert(
            "GERAL", {"k": 12, "cacheavel": False}, versao_esperada=1, atualizado_por="admin",
        )
        await s.commit()
    assert cfg.versao == 2 and cfg.k == 12 and cfg.cacheavel is False

    async with AsyncSessionLocal() as s:
        repo = RouteRegistryRepository(s)
        cfg = await repo.obter("GERAL")
        hist = await repo.historico("GERAL")
    assert cfg.k == 12 and cfg.cacheavel is False and cfg.versao == 2
    assert [h["versao"] for h in hist] == [2, 1]
    assert hist[0]["snapshot"]["k"] == 12
    assert hist[1]["snapshot"]["k"] == 6  # baseline


async def test_segunda_escrita_com_versao_obsoleta_recebe_conflito(db_limpo):
    async with AsyncSessionLocal() as s:
        await RouteRegistryRepository(s).upsert("SIGAA", {"k": 5}, versao_esperada=1, atualizado_por="A")
        await s.commit()

    with pytest.raises(ConflitoDeVersao) as ei:
        async with AsyncSessionLocal() as s:
            await RouteRegistryRepository(s).upsert("SIGAA", {"k": 9}, versao_esperada=1, atualizado_por="B")
            await s.commit()
    assert ei.value.atual == 2

    async with AsyncSessionLocal() as s:
        cfg = await RouteRegistryRepository(s).obter("SIGAA")
    assert cfg.k == 5  # a escrita de A sobreviveu


async def test_reverter_restaura_snapshot_da_versao(db_limpo):
    async with AsyncSessionLocal() as s:
        await RouteRegistryRepository(s).upsert(
            "CALENDARIO", {"k": 99, "doc_type": "outro"}, versao_esperada=1, atualizado_por="a",
        )
        await s.commit()

    async with AsyncSessionLocal() as s:
        repo = RouteRegistryRepository(s)
        snap = await repo.snapshot_da_versao("CALENDARIO", 1)
        campos = {k: snap[k] for k in rr.CAMPOS_EDITAVEIS if k in snap}
        cur = await repo.obter("CALENDARIO")
        nv = await repo.upsert("CALENDARIO", campos, versao_esperada=cur.versao, atualizado_por="revert")
        await s.commit()

    assert nv.versao == 3
    async with AsyncSessionLocal() as s:
        cfg = await RouteRegistryRepository(s).obter("CALENDARIO")
    assert cfg.k == 8 and cfg.doc_type == "calendario"


async def test_aget_repara_espelho_no_miss(db_limpo, fake_redis):
    assert fake_redis.get("route:EDITAL") is None
    cfg = await rr.aget("EDITAL")
    assert cfg.k == 10
    assert fake_redis.get("route:EDITAL") is not None


async def test_aget_com_postgres_fora_cai_no_default(monkeypatch, fake_redis):
    def _boom(*a, **k):
        raise ConnectionError("PG simulado fora")
    monkeypatch.setattr("src.infrastructure.database.session.AsyncSessionLocal", _boom)
    cfg = await rr.aget("WIKI")
    assert cfg.entrypoint_node == "rag" and cfg.doc_type == "wiki_ctic"


async def test_hydrate_espelha_as_11_rotas(db_limpo, fake_redis):
    n = await rr.hydrate_redis()
    assert n == 11
    assert fake_redis.get("route:GERAL") is not None
