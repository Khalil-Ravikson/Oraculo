"""
src/capabilities/registry.py
===============================
Ex `domain/tools/crud_tools.py` + `domain/tools/tool_registry.py` (este
último já deletado na Fase 1 — era `StructuredTool` factory morta; o
mecanismo escolhido aqui é o decorator+dict de `crud_tools.py`, mais simples
e com um padrão real de uso). Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md,
seção 2.5.

Sprint 2 (Fase 2): ganhou autodiscovery via `pkgutil`, mesmo padrão de
`application/workers/registry.py::_autodiscover_workers()` — as tools
concretas moraram em `capabilities/tools/tool_*.py` (ver débito técnico
documentado lá) em vez de hardcoded neste arquivo.

ACHADO da Fase 6 original: nenhum destes tools tinha consumidor vivo — o
único import de `crud_tools.executar_tool` era em
`application/chain/oracle_chain.bak` (arquivo `.bak`, nunca executado). A
rota "CRUD" do Supervisor aponta hoje para um worker "crud_confirm" que não
existe (ver `agents/tickets/service.py`). Migrado mesmo assim porque a
implementação é válida e reaproveitável — só não está conectada a nenhum
fluxo de produção no momento. Conectar isso é trabalho de produto (decidir
COMO o CRUD confirma e dispara), não desta fase estrutural.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

logger = logging.getLogger(__name__)

_TOOL_REGISTRY: dict[str, callable] = {}
_TOOLS_LOADED: bool = False


def tool(name: str):
    """Decorator para registrar capabilities por nome."""
    def decorator(fn):
        _TOOL_REGISTRY[name] = fn
        return fn
    return decorator


def _autodiscover_tools():
    """Escaneia `capabilities/tools/` e importa todo módulo `tool_*.py`,
    disparando os decoradores `@tool(...)`. Roda uma única vez (lazy)."""
    global _TOOLS_LOADED
    if _TOOLS_LOADED:
        return

    import src.capabilities.tools as tools_pkg

    for _, module_name, is_pkg in pkgutil.iter_modules(tools_pkg.__path__):
        if not is_pkg and module_name.startswith("tool_"):
            full_module_name = f"src.capabilities.tools.{module_name}"
            try:
                importlib.import_module(full_module_name)
            except Exception as e:
                logger.error("❌ [CAPABILITIES REGISTRY] Falha ao auto-importar %s: %s", full_module_name, e)

    _TOOLS_LOADED = True


async def executar_tool(tool_name: str, args: dict) -> dict:
    """Dispatcher central de capabilities registradas."""
    _autodiscover_tools()
    fn = _TOOL_REGISTRY.get(tool_name)
    if not fn:
        raise ValueError(f"Tool '{tool_name}' não encontrada.")
    return await fn(**args)


def available() -> list[str]:
    _autodiscover_tools()
    return list(_TOOL_REGISTRY.keys())
