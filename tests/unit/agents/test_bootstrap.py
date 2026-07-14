# tests/unit/agents/test_bootstrap.py
"""
Testa que register_all_agents() (Fase 5 do PLANO_REFATORACAO_SUPERVISOR.md)
efetivamente popula o AgentRegistry com os agentes concretos criados até
agora (AcademicKnowledgeAgent da Fase 4, SigaaAgent da Fase 5) — fechando a
lacuna deixada na Fase 4, onde a classe existia mas nunca era registrada.
"""
import src.agents.bootstrap as bootstrap_module
from src.agents.registry import registry


def test_register_all_agents_populates_registry():
    bootstrap_module._REGISTERED = False
    for nome in ("academic_knowledge", "sigaa"):
        registry._agents.pop(nome, None)

    bootstrap_module.register_all_agents()

    nomes = {a.name for a in registry.all()}
    assert "academic_knowledge" in nomes
    assert "sigaa" in nomes


def test_register_all_agents_e_idempotente():
    bootstrap_module._REGISTERED = False
    for nome in ("academic_knowledge", "sigaa"):
        registry._agents.pop(nome, None)

    bootstrap_module.register_all_agents()
    qtd_apos_primeira_chamada = len(registry.all())
    bootstrap_module.register_all_agents()

    assert len(registry.all()) == qtd_apos_primeira_chamada
