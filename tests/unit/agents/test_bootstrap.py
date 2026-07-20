# tests/unit/agents/test_bootstrap.py
"""
Testa que register_all_agents() (Fase 5/6 do PLANO_REFATORACAO_SUPERVISOR.md;
Sprint 2 Fase 4 tornou a função async por causa do upsert best-effort no
catálogo Postgres) efetivamente popula o AgentRegistry com os agentes
concretos criados até agora (AcademicKnowledgeAgent da Fase 4, SigaaAgent da
Fase 5, ConversationAgent e TicketAgent da Fase 6) — fechando a lacuna
deixada na Fase 4, onde a classe existia mas nunca era registrada.
"""
import pytest

import src.agents.bootstrap as bootstrap_module
from src.agents.registry import registry

_NOMES_ESPERADOS = ("academic_knowledge", "sigaa", "conversation", "tickets")


@pytest.mark.asyncio
async def test_register_all_agents_populates_registry():
    bootstrap_module._REGISTERED = False
    for nome in _NOMES_ESPERADOS:
        registry._agents.pop(nome, None)

    await bootstrap_module.register_all_agents()

    nomes = {a.name for a in registry.all()}
    for esperado in _NOMES_ESPERADOS:
        assert esperado in nomes


@pytest.mark.asyncio
async def test_register_all_agents_e_idempotente():
    bootstrap_module._REGISTERED = False
    for nome in _NOMES_ESPERADOS:
        registry._agents.pop(nome, None)

    await bootstrap_module.register_all_agents()
    qtd_apos_primeira_chamada = len(registry.all())
    await bootstrap_module.register_all_agents()

    assert len(registry.all()) == qtd_apos_primeira_chamada


@pytest.mark.asyncio
async def test_register_all_agents_nao_quebra_se_postgres_falhar(monkeypatch):
    """O upsert no catálogo Postgres é best-effort — falha no Postgres não
    pode impedir o registro dos agentes em memória (AgentRegistry)."""
    bootstrap_module._REGISTERED = False
    for nome in _NOMES_ESPERADOS:
        registry._agents.pop(nome, None)

    class _SessionLocalQueBrarra:
        def __call__(self, *args, **kwargs):
            raise ConnectionError("Postgres indisponível (simulado)")

    monkeypatch.setattr(
        "src.infrastructure.database.session.AsyncSessionLocal",
        _SessionLocalQueBrarra(),
    )

    await bootstrap_module.register_all_agents()

    nomes = {a.name for a in registry.all()}
    for esperado in _NOMES_ESPERADOS:
        assert esperado in nomes
