# tests/unit/capabilities/test_tools_registry.py
"""
Confirma o autodiscovery via pkgutil de `capabilities/registry.py` (Sprint 2,
Fase 2) — mesmo padrão de `application/workers/registry.py`.

Nota: os testes não fazem `_TOOL_REGISTRY.clear()` + reimport, porque
`importlib.import_module()` não reexecuta um módulo já presente em
`sys.modules` — o decorator `@tool(...)` só dispara uma vez por processo.
"""
import pytest

from src.capabilities import registry


def test_autodiscover_popula_as_3_tools_existentes():
    nomes = registry.available()

    assert "update_student_email" in nomes
    assert "update_student_telefone" in nomes
    assert "get_student_info" in nomes


def test_available_e_idempotente():
    primeira = registry.available()
    segunda = registry.available()

    assert primeira == segunda


@pytest.mark.asyncio
async def test_executar_tool_falha_para_tool_inexistente():
    with pytest.raises(ValueError):
        await registry.executar_tool("tool_que_nao_existe", {})
