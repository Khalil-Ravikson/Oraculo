"""
src/application/orchestration/routers.py
=======================================
Registro de *routers* de aresta condicional — funções puras `state -> str`
que decidem, em runtime, qual caminho o grafo toma a partir de um nó.

ADR 0008 Fase 5 (`GraphSpec`): a topologia do grafo virou dado
(`specs/default.json` / tabela `graph_spec`), mas a lógica das arestas
condicionais NÃO vira string — continua sendo Python, versionada com o
código. A `EdgeSpec` referencia um router por nome (`when: "by_state_route"`);
o `builder` resolve o nome pra função aqui.

Cada router recebe o `OraculoState` (instância Pydantic, dentro do grafo) e
devolve uma string — o `route_value` de uma das arestas que saem daquele nó.
"""
from __future__ import annotations

from typing import Callable

from src.application.orchestration.state import OraculoState

_ROUTERS: dict[str, Callable[[OraculoState], str]] = {}


def register_router(nome: str):
    def _deco(fn: Callable[[OraculoState], str]) -> Callable[[OraculoState], str]:
        if nome in _ROUTERS:
            raise ValueError(f"router duplicado: {nome}")
        _ROUTERS[nome] = fn
        return fn
    return _deco


def get_router(nome: str) -> Callable[[OraculoState], str]:
    try:
        return _ROUTERS[nome]
    except KeyError:
        raise ValueError(
            f"router '{nome}' não registrado. Disponíveis: {sorted(_ROUTERS)}"
        )


def nomes_registrados() -> frozenset[str]:
    return frozenset(_ROUTERS)


# ── Fan-out da classificação ────────────────────────────────────────────────

@register_router("by_state_route")
def _by_state_route(state: OraculoState) -> str:
    """`state.route` — o `entrypoint_node` da rota classificada
    (`route_registry.get(rota).entrypoint_node`), posto no state pelo
    `classify_node`. Vale "rag" | "ticket" | "crud" | "greeting" | "sigaa" |
    "media_download" | "check_status" | "human_handoff"."""
    return state.route


# ── Funil de ticket (validação com re-pergunta) ─────────────────────────────
# Reaproveitam os validadores puros de `nodes.py` — um único lugar de verdade
# por regra (o próprio nó também os chama pra decidir avançar/re-perguntar).

@register_router("ticket_tipo_valido")
def _ticket_tipo_valido(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _tipo_valido
    return _tipo_valido(state)


@register_router("ticket_categoria_valida")
def _ticket_categoria_valida(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _categoria_valida
    return _categoria_valida(state)


@register_router("ticket_queixa_valida")
def _ticket_queixa_valida(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _queixa_valida
    return _queixa_valida(state)


@register_router("ticket_confirm_route")
def _ticket_confirm_route(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _confirm_route
    return _confirm_route(state)


# ── Funil de CRUD de cadastro ──────────────────────────────────────────────

@register_router("crud_campo_valido")
def _crud_campo_valido(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _campo_crud_valido
    return _campo_crud_valido(state)


@register_router("crud_valor_valido")
def _crud_valor_valido(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _valor_crud_valido
    return _valor_crud_valido(state)


@register_router("crud_confirm_route")
def _crud_confirm_route_reg(state: OraculoState) -> str:
    from src.application.orchestration.nodes import _crud_confirm_route
    return _crud_confirm_route(state)
