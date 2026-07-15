# tests/unit/capabilities/persistence/test_agent_config.py
"""
Testes de src/capabilities/persistence/agent_config.py — a convenção de
liga/desliga por agente criada para o painel /hub/agents.

`set_agent_enabled` é async desde a Sprint 2 Fase 5 (dual-write best-effort
no catálogo Postgres) — a leitura (`is_agent_enabled`/`status_de_todos`)
continua síncrona e 100% Redis nesta fase.
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


def test_agente_ativo_por_padrao_quando_chave_ausente():
    redis = FakeRedis()
    assert is_agent_enabled(redis, "sigaa") is True


@pytest.mark.asyncio
async def test_set_enabled_false_desativa():
    redis = FakeRedis()
    await set_agent_enabled(redis, "sigaa", False)
    assert is_agent_enabled(redis, "sigaa") is False


@pytest.mark.asyncio
async def test_set_enabled_true_reativa():
    redis = FakeRedis()
    await set_agent_enabled(redis, "sigaa", False)
    await set_agent_enabled(redis, "sigaa", True)
    assert is_agent_enabled(redis, "sigaa") is True


def test_falha_no_redis_assume_ativo():
    class RedisQuebrado:
        def get(self, key):
            raise ConnectionError("redis fora do ar")

    assert is_agent_enabled(RedisQuebrado(), "sigaa") is True


@pytest.mark.asyncio
async def test_status_de_todos_agrega_varios_agentes():
    redis = FakeRedis()
    await set_agent_enabled(redis, "tickets", False)

    status = status_de_todos(redis, ["sigaa", "tickets", "conversation"])

    assert status == {"sigaa": True, "tickets": False, "conversation": True}


@pytest.mark.asyncio
async def test_set_enabled_nao_quebra_se_postgres_falhar(monkeypatch):
    """Dual-write é best-effort — falha no Postgres não pode impedir a
    escrita no Redis (que é a fonte de verdade até a Fase 6)."""
    class _SessionLocalQueBrarra:
        def __call__(self, *args, **kwargs):
            raise ConnectionError("Postgres indisponível (simulado)")

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        _SessionLocalQueBrarra(),
    )

    redis = FakeRedis()
    await set_agent_enabled(redis, "sigaa", False, admin="fulano")

    assert is_agent_enabled(redis, "sigaa") is False
