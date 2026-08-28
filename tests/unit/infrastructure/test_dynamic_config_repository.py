"""
Plano A / Fase 1 — `DynamicConfigRepository` + read-repair de `dynamic_config`,
contra Postgres real (o CI provê `postgres:16` na porta 5433 e roda
`alembic upgrade head` antes da suíte; localmente, `docker` equivalente).

Cobre os itens de "definition of done" da §T que precisam de banco real:
  * concorrência: a 2ª escrita com versão obsoleta recebe `ConflitoDeVersao`
  * histórico append-only: cada escrita insere uma linha, v1 baseline incluída
  * read-repair: um MISS no Redis relê o Postgres e reescreve o espelho
  * degradação: Postgres fora do ar → `aget_*` cai no default, sem exceção

Se o Postgres de teste não estiver acessível, os testes são pulados (não falham)
— mesma tolerância dos outros testes de infra do repo.
"""
import pytest

from src.infrastructure import dynamic_config as dc
from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.repositories.dynamic_config_repository import (
    ConflitoDeVersao,
    DynamicConfigRepository,
)
from src.infrastructure.settings import settings
from tests.unit.infrastructure.test_dynamic_config import SEED_009

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_SEED = SEED_009  # [(chave, tipo, valor), ...] — fonte única: a migration 009


class _FakeRedisText:
    def __init__(self, dados=None):
        self._d = dict(dados or {})

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v):
        self._d[k] = str(v)


@pytest.fixture
def db_limpo():
    """Reseta `config_dinamica` / `_historico` ao estado de seed antes de
    cada teste, via psycopg2 síncrono (sem event loop — o repo assíncrono é
    exercido pelos testes, não pela fixture). Pula se o Postgres não responder."""
    import psycopg2

    url = settings.DATABASE_URL.replace("+asyncpg", "")
    try:
        conn = psycopg2.connect(url)
    except Exception as exc:  # pragma: no cover - ambiente sem Postgres
        pytest.skip(f"Postgres de teste indisponível: {exc}")

    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE config_dinamica, config_dinamica_historico RESTART IDENTITY")
        for chave, tipo, valor in _SEED:
            cur.execute(
                "INSERT INTO config_dinamica (chave, tipo, valor, versao) VALUES (%s, %s, %s, 1)",
                (chave, tipo, valor),
            )
            cur.execute(
                "INSERT INTO config_dinamica_historico (chave, valor_antigo, valor_novo, versao) "
                "VALUES (%s, NULL, %s, 1)",
                (chave, valor),
            )
    conn.close()
    yield


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedisText()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: fake)
    return fake


# ─── Concorrência (§N item 1 / §T) ──────────────────────────────────────────

async def test_segunda_escrita_com_versao_obsoleta_recebe_conflito(db_limpo):
    async with AsyncSessionLocal() as s:
        nova = await DynamicConfigRepository(s).upsert(
            "GEMINI_MODEL", "gemini-2.5-pro", "str", versao_esperada=1, atualizado_por="admin-A",
        )
        await s.commit()
    assert nova == 2

    # admin B ainda tinha a v1 na tela
    with pytest.raises(ConflitoDeVersao) as ei:
        async with AsyncSessionLocal() as s:
            await DynamicConfigRepository(s).upsert(
                "GEMINI_MODEL", "gemini-2.5-flash-lite", "str", versao_esperada=1, atualizado_por="admin-B",
            )
            await s.commit()
    assert ei.value.esperada == 1 and ei.value.atual == 2

    # o valor de A sobreviveu — last-write-wins não apagou nada
    async with AsyncSessionLocal() as s:
        atual = await DynamicConfigRepository(s).obter("GEMINI_MODEL")
    assert atual["valor"] == "gemini-2.5-pro" and atual["versao"] == 2


# ─── Histórico append-only (§N item 3) ─────────────────────────────────────

async def test_historico_registra_cada_escrita_com_valor_antigo_e_novo(db_limpo):
    async with AsyncSessionLocal() as s:
        repo = DynamicConfigRepository(s)
        await repo.upsert("RAG_CACHE_TTL_SECONDS", "1800", "int", versao_esperada=1, atualizado_por="a")
        await s.commit()
    async with AsyncSessionLocal() as s:
        repo = DynamicConfigRepository(s)
        await repo.upsert("RAG_CACHE_TTL_SECONDS", "900", "int", versao_esperada=2, atualizado_por="b")
        await s.commit()

    async with AsyncSessionLocal() as s:
        hist = await DynamicConfigRepository(s).historico("RAG_CACHE_TTL_SECONDS")

    assert [(h["versao"], h["valor_antigo"], h["valor_novo"]) for h in hist] == [
        (3, "1800", "900"),
        (2, "3600", "1800"),
        (1, None, "3600"),
    ]


async def test_reverter_usa_valor_da_versao_alvo(db_limpo):
    async with AsyncSessionLocal() as s:
        repo = DynamicConfigRepository(s)
        await repo.upsert("GEMINI_MODEL", "gemini-2.5-pro", "str", versao_esperada=1, atualizado_por="a")
        await s.commit()

    async with AsyncSessionLocal() as s:
        repo = DynamicConfigRepository(s)
        alvo = await repo.valor_na_versao("GEMINI_MODEL", 1)
        atual = await repo.obter("GEMINI_MODEL")
        nova = await repo.upsert("GEMINI_MODEL", alvo, "str", versao_esperada=atual["versao"], atualizado_por="revert")
        await s.commit()

    assert alvo == "gemini-2.5-flash" and nova == 3
    async with AsyncSessionLocal() as s:
        assert (await DynamicConfigRepository(s).obter("GEMINI_MODEL"))["valor"] == "gemini-2.5-flash"


# ─── Read-repair / drift (§N item 2 / §T) ──────────────────────────────────

async def test_aget_repara_o_espelho_no_miss(db_limpo, fake_redis):
    # Redis vazio (escrita do espelho falhou / Redis reiniciou).
    assert fake_redis.get("config:GEMINI_MODEL") is None

    valor = await dc.aget_str("GEMINI_MODEL")

    assert valor == "gemini-2.5-flash"
    assert fake_redis.get("config:GEMINI_MODEL") == "gemini-2.5-flash"  # espelho reparado


async def test_aget_reflete_valor_novo_apos_escrita(db_limpo, fake_redis):
    async with AsyncSessionLocal() as s:
        await DynamicConfigRepository(s).upsert(
            "RAG_CACHE_TTL_SECONDS", "1200", "int", versao_esperada=1, atualizado_por="a",
        )
        await s.commit()
    # espelho ainda não sabe — aget lê Postgres no miss
    assert await dc.aget_int("RAG_CACHE_TTL_SECONDS") == 1200


# ─── Degradação: Postgres fora do ar (§T) ──────────────────────────────────

async def test_aget_com_postgres_fora_cai_no_default(monkeypatch, fake_redis):
    def _boom(*a, **k):
        raise ConnectionError("Postgres indisponível (simulado)")
    monkeypatch.setattr("src.infrastructure.database.session.AsyncSessionLocal", _boom)

    # Redis vazio + Postgres fora → default de settings, sem exceção
    assert await dc.aget_int("RAG_CACHE_TTL_SECONDS") == settings.RAG_CACHE_TTL_SECONDS
    assert await dc.aget_str("GEMINI_MODEL") == settings.GEMINI_MODEL


async def test_hydrate_redis_espelha_todas_as_chaves(db_limpo, fake_redis):
    n = await dc.hydrate_redis()
    assert n == 7
    assert fake_redis.get("config:GEMINI_MODEL") == "gemini-2.5-flash"
    assert fake_redis.get("config:RAG_RERANKER_ENABLED") == "true"
