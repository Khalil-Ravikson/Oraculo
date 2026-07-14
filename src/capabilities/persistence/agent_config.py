"""
src/capabilities/persistence/agent_config.py
================================================
Primeira convenção de config-por-agente no Redis, criada para o painel
`/hub/agents`: liga/desliga por agente, checado em `BaseAgent.can_execute()`.

Chave: `admin:agent:{nome}:enabled` — string "0" desativa; qualquer outro
valor (inclusive chave ausente) é tratado como ativo, para que agentes
recém-registrados não fiquem desligados por padrão.
"""
from __future__ import annotations


def _chave(nome: str) -> str:
    return f"admin:agent:{nome}:enabled"


def is_agent_enabled(redis, nome: str) -> bool:
    """Ativo por padrão — só é desativado se a chave existir com valor '0'."""
    try:
        raw = redis.get(_chave(nome))
    except Exception:
        return True
    if raw is None:
        return True
    valor = raw if isinstance(raw, str) else raw.decode()
    return valor != "0"


def set_agent_enabled(redis, nome: str, enabled: bool) -> None:
    redis.set(_chave(nome), "1" if enabled else "0")


def status_de_todos(redis, nomes: list[str]) -> dict[str, bool]:
    return {nome: is_agent_enabled(redis, nome) for nome in nomes}
