"""
src/capabilities/registry.py — Tool/Capability Registry
================================================================================
Registro EXPLÍCITO de capabilities (via decorator `@tool`), com autodiscovery
de `capabilities/tools/tool_*.py` (pkgutil). Plano A / Fase 5: cada entrada
carrega um **manifesto** (§S) — nome, versão de interface, permissões que
precisa, se exige confirmação — em vez de um dict cru `{nome: fn}`.

NÃO é plugin architecture com código externo — continua registro no mesmo
pacote, pelo mesmo time. O vínculo agente↔capability é dado (tabela
`agente_tools`, migration 012), não código; ver `capabilities/agent_tools.py`.

Histórico: até a Fase 5 as 3 tools estavam quebradas (importavam
`infrastructure/database/connection` e `repositories/postgres_user_repository`,
módulos que não existem) — `_autodiscover_tools` engolia o ImportError e
nada registrava. Consertado nesta fase.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_INTERFACE = "ICapability/1"

_TOOL_REGISTRY: dict[str, "callable"] = {}
_MANIFESTS: dict[str, "CapabilityManifest"] = {}
_TOOLS_LOADED: bool = False


@dataclass(frozen=True)
class CapabilityManifest:
    nome: str
    descricao: str
    interface: str
    permissoes: tuple[str, ...]
    confirmacao: bool          # exige confirmação HITL antes de executar?


def tool(nome: str, *, descricao: str = "", permissoes=(), confirmacao: bool = False):
    """Registra uma capability + seu manifesto."""
    def decorator(fn):
        _TOOL_REGISTRY[nome] = fn
        _MANIFESTS[nome] = CapabilityManifest(
            nome=nome,
            descricao=descricao or (fn.__doc__ or "").strip().splitlines()[0].strip() or nome,
            interface=_INTERFACE,
            permissoes=tuple(permissoes),
            confirmacao=confirmacao,
        )
        return fn
    return decorator


def _autodiscover_tools() -> None:
    """Importa todo `capabilities/tools/tool_*.py`, disparando os `@tool(...)`.
    Roda uma única vez (lazy)."""
    global _TOOLS_LOADED
    if _TOOLS_LOADED:
        return

    import src.capabilities.tools as tools_pkg

    for _, module_name, is_pkg in pkgutil.iter_modules(tools_pkg.__path__):
        if not is_pkg and module_name.startswith("tool_"):
            full = f"src.capabilities.tools.{module_name}"
            try:
                importlib.import_module(full)
            except Exception as e:
                logger.error("❌ [CAPABILITIES REGISTRY] Falha ao auto-importar %s: %s", full, e)

    _TOOLS_LOADED = True


async def executar_tool(tool_name: str, args: dict) -> dict:
    """Dispatcher central de capabilities. Primeiro o registro de código
    (`@tool`), depois o catálogo por dado (`tools_catalogo`, migration 016,
    Hub v2) — uma ferramenta criada pelo painel roda pelo
    `dynamic_tool_executor` sem precisar de arquivo `tool_*.py`."""
    _autodiscover_tools()
    fn = _TOOL_REGISTRY.get(tool_name)
    if fn:
        return await fn(**args)

    # Ferramenta por dado (painel). `executar` levanta ToolNaoEncontradaError
    # (subclasse de ValueError) se o nome também não existe no catálogo —
    # preserva o contrato antigo de "tool desconhecida → ValueError".
    from src.capabilities import dynamic_tool_executor
    return await dynamic_tool_executor.executar(tool_name, args)


def available() -> list[str]:
    _autodiscover_tools()
    return list(_TOOL_REGISTRY.keys())


def manifesto(nome: str) -> CapabilityManifest | None:
    _autodiscover_tools()
    return _MANIFESTS.get(nome)


def manifestos() -> list[CapabilityManifest]:
    _autodiscover_tools()
    return list(_MANIFESTS.values())
