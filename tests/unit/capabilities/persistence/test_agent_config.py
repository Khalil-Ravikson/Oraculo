# tests/unit/capabilities/persistence/test_agent_config.py
"""
Testes de src/capabilities/persistence/agent_config.py — a convenção de
liga/desliga por agente criada para o painel /hub/agents.
"""
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


def test_set_enabled_false_desativa():
    redis = FakeRedis()
    set_agent_enabled(redis, "sigaa", False)
    assert is_agent_enabled(redis, "sigaa") is False


def test_set_enabled_true_reativa():
    redis = FakeRedis()
    set_agent_enabled(redis, "sigaa", False)
    set_agent_enabled(redis, "sigaa", True)
    assert is_agent_enabled(redis, "sigaa") is True


def test_falha_no_redis_assume_ativo():
    class RedisQuebrado:
        def get(self, key):
            raise ConnectionError("redis fora do ar")

    assert is_agent_enabled(RedisQuebrado(), "sigaa") is True


def test_status_de_todos_agrega_varios_agentes():
    redis = FakeRedis()
    set_agent_enabled(redis, "tickets", False)

    status = status_de_todos(redis, ["sigaa", "tickets", "conversation"])

    assert status == {"sigaa": True, "tickets": False, "conversation": True}
