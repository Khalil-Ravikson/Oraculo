"""
Plano A / Fase 3 — `llm_provider_registry` (§D/§S).
"""
import pytest

from src.infrastructure.adapters import llm_provider_registry as reg


def test_os_3_providers_estao_registrados():
    assert set(reg.registrados()) == {"gemini", "deepseek", "groq"}


def test_instanciar_provider_desconhecido_levanta():
    with pytest.raises(ValueError):
        reg.instanciar("openai")


def test_manifesto_tem_versao_de_interface():
    m = reg.manifesto("gemini")
    assert m is not None and m.interface_version == "ILLMProvider/1"
    assert m.health_check is not None


def test_health_check_reflete_credencial(monkeypatch):
    from src.infrastructure.settings import settings
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-xxx", raising=False)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "", raising=False)
    assert reg.health_check("deepseek") is True
    assert reg.health_check("groq") is False
    assert reg.health_check("inexistente") is None


def test_status_lista_todos_com_saude():
    s = reg.status()
    assert {x["nome"] for x in s} == {"gemini", "deepseek", "groq"}
    assert all("interface" in x and "saude" in x for x in s)


def test_instanciar_gemini_devolve_provider(monkeypatch):
    from src.infrastructure.settings import settings
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "x", raising=False)
    p = reg.instanciar("gemini", modelo="gemini-2.5-flash")
    assert getattr(p, "provider_name", "") == "gemini"


def test_llm_factory_usa_o_registry():
    from src.infrastructure.adapters import llm_factory
    # `_providers_validos()` é função (não constante) desde o Hub v2 —
    # um provedor cadastrado pelo painel precisa ser selecionável sem restart.
    # Sem espelho Redis (ambiente de teste), volta aos 3 nativos.
    assert set(llm_factory._providers_validos()) == {"gemini", "deepseek", "groq"}
