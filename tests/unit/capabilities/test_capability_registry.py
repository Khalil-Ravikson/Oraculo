"""
Plano A / Fase 5 — capabilities/registry.py: autodiscovery volta a funcionar
(as 3 tools estavam quebradas) + manifesto (§S).
"""
import pytest

from src.capabilities import registry


def test_as_3_capabilities_registram():
    nomes = set(registry.available())
    assert nomes == {"get_student_info", "update_student_email", "update_student_telefone"}


def test_manifesto_tem_permissoes_e_confirmacao():
    m = registry.manifesto("update_student_email")
    assert m is not None
    assert m.interface == "ICapability/1"
    assert m.permissoes == ("pessoa:write",)
    assert m.confirmacao is True   # escrita → exige confirmação

    leitura = registry.manifesto("get_student_info")
    assert leitura.permissoes == ("pessoa:read",)
    assert leitura.confirmacao is False


def test_manifestos_lista_todos():
    assert {m.nome for m in registry.manifestos()} == set(registry.available())


@pytest.mark.asyncio
async def test_executar_tool_desconhecida_levanta():
    with pytest.raises(ValueError):
        await registry.executar_tool("nao_existe", {})
