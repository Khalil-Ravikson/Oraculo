# tests/unit/capabilities/persistence/test_agent_config.py
"""
Testes de src/capabilities/persistence/agent_config.py — a convenção de
liga/desliga por agente criada para o painel /hub/agents.

`set_agent_enabled` (Fase 5) e `is_agent_enabled`/`status_de_todos`
(Fase 6) são async: a leitura consulta o catálogo Postgres primeiro,
caindo para Redis se o Postgres falhar ou não tiver linha para o agente.
Neste ambiente de teste o Postgres é inalcançável (sem mock), então os
testes que não mockam `AgentCatalogRepository` exercitam naturalmente o
fallback Redis.
"""
import pytest

from src.capabilities.persistence.agent_config import (
    is_agent_enabled,
    set_agent_enabled,
    status_de_todos,
)


class FakeRedis:
    def __init__(self):
        self.db = {}

    def get(self, key):
        return self.db.get(key)

    def set(self, key, value):
        self.db[key] = value


@pytest.mark.asyncio
async def test_agente_ativo_por_padrao_quando_chave_ausente():
    redis = FakeRedis()
    assert await is_agent_enabled(redis, "sigaa") is True


@pytest.mark.asyncio
async def test_set_enabled_false_desativa():
    redis = FakeRedis()
    await set_agent_enabled(redis, "sigaa", False)
    assert await is_agent_enabled(redis, "sigaa") is False


@pytest.mark.asyncio
async def test_set_enabled_true_reativa():
    redis = FakeRedis()
    await set_agent_enabled(redis, "sigaa", False)
    await set_agent_enabled(redis, "sigaa", True)
    assert await is_agent_enabled(redis, "sigaa") is True


@pytest.mark.asyncio
async def test_falha_no_redis_e_no_postgres_assume_ativo():
    class RedisQuebrado:
        def get(self, key):
            raise ConnectionError("redis fora do ar")

    assert await is_agent_enabled(RedisQuebrado(), "sigaa") is True


@pytest.mark.asyncio
async def test_status_de_todos_agrega_varios_agentes():
    redis = FakeRedis()
    await set_agent_enabled(redis, "tickets", False)

    status = await status_de_todos(redis, ["sigaa", "tickets", "conversation"])

    assert status == {"sigaa": True, "tickets": False, "conversation": True}


@pytest.mark.asyncio
async def test_set_enabled_nao_quebra_se_postgres_falhar(monkeypatch):
    """Dual-write é best-effort — falha no Postgres não pode impedir a
    escrita no Redis."""
    class _SessionLocalQueBrarra:
        def __call__(self, *args, **kwargs):
            raise ConnectionError("Postgres indisponível (simulado)")

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        _SessionLocalQueBrarra(),
    )

    redis = FakeRedis()
    await set_agent_enabled(redis, "sigaa", False, admin="fulano")

    assert await is_agent_enabled(redis, "sigaa") is False


@pytest.mark.asyncio
async def test_is_agent_enabled_usa_postgres_quando_disponivel(monkeypatch):
    """Postgres manda mesmo se o Redis disser o contrário (Fase 6)."""
    from src.infrastructure.repositories.agent_catalog_repository import AgentCatalogRepository

    async def _obter_fake(self, nome):
        return {"nome": nome, "ativo": False, "descricao": None,
                "permissions": [], "atualizado_em": None, "atualizado_por": None}

    monkeypatch.setattr(AgentCatalogRepository, "obter", _obter_fake)

    class _SessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        lambda: _SessionCtx(),
    )

    redis = FakeRedis()
    redis.set("admin:agent:sigaa:enabled", "1")  # Redis diz ativo

    assert await is_agent_enabled(redis, "sigaa") is False  # Postgres manda


@pytest.mark.asyncio
async def test_is_agent_enabled_cai_para_redis_se_postgres_falhar(monkeypatch):
    class _SessionLocalQueBrarra:
        def __call__(self, *args, **kwargs):
            raise ConnectionError("Postgres indisponível (simulado)")

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        _SessionLocalQueBrarra(),
    )

    redis = FakeRedis()
    redis.set("admin:agent:sigaa:enabled", "0")

    assert await is_agent_enabled(redis, "sigaa") is False
