"""
src/infrastructure/scraping/implementations/dokuwiki/hierarchy.py
----------------------------------------------------------------------
Reconstrução da hierarquia (Portal → Sistema → Módulo → Tutorial) do wiki
CTIC, que NÃO está codificada nos page_ids (a maioria é flat, sem namespace
aninhado — confirmado via `do=index`). A única fonte real de hierarquia é o
grafo de links entre páginas: quem linka para quem.

Estratégia:
  1. Cada vez que uma página é processada, registra os links [[filho]] dela
     em `wiki:children:{page_id}` (Redis set) e o pai de cada filho em
     `wiki:parent:{filho}` (só grava se ainda não houver um pai registrado —
     primeira página que linka "vence", evita hubs genéricos como `start`
     sobrescreverem um pai mais específico).
  2. `KNOWN_SYSTEM_HUBS` mapeia page_ids de hub conhecidos (ex. `almoxarifado`)
     para (sistema, modulo). `resolver_taxonomia()` sobe a cadeia de pais
     (via Redis) até achar um hub conhecido ou esgotar `max_depth`.

Sem Redis injetado, tudo funciona em memória (dict), útil para testes.
"""
from __future__ import annotations

from typing import Protocol


# page_id de hub → (sistema, modulo). Curadoria manual, crescer conforme
# novas páginas de módulo forem descobertas no wiki.
KNOWN_SYSTEM_HUBS: dict[str, tuple[str, str]] = {
    "modulos-sipac": ("SIPAC", "Geral"),
    "almoxarifado": ("SIPAC", "Almoxarifado"),
    "catalogo_de_materiais": ("SIPAC", "Catalogo de Materiais"),
    "compras_e_licitacoes": ("SIPAC", "Compras e Licitacoes"),
    "contratos": ("SIPAC", "Contratos"),
    "orcamento": ("SIPAC", "Orcamento"),
    "patrimonio_movel": ("SIPAC", "Patrimonio Movel"),
    "protocolo": ("SIPAC", "Protocolo"),
    "atendimento_de_requisicoes": ("SIPAC", "Atendimento de Requisicoes"),
    "siguema": ("SIGUEMA", "Geral"),
}

DEFAULT_SISTEMA = "Geral"
DEFAULT_MODULO = "Geral"


class GraphStore(Protocol):
    def get_parent(self, page_id: str) -> str | None: ...
    def set_parent_if_absent(self, child_id: str, parent_id: str) -> None: ...


class InMemoryGraphStore:
    """Implementação sem Redis — usada em testes e como default."""

    def __init__(self) -> None:
        self._parents: dict[str, str] = {}

    def get_parent(self, page_id: str) -> str | None:
        return self._parents.get(page_id)

    def set_parent_if_absent(self, child_id: str, parent_id: str) -> None:
        self._parents.setdefault(child_id, parent_id)


class RedisGraphStore:
    """Persiste o grafo pai→filho em Redis (`wiki:parent:{page_id}`)."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    def get_parent(self, page_id: str) -> str | None:
        return self._redis.get(f"wiki:parent:{page_id}")

    def set_parent_if_absent(self, child_id: str, parent_id: str) -> None:
        self._redis.set(f"wiki:parent:{child_id}", parent_id, nx=True)


def registrar_links(page_id: str, links_filhos: list[str], store: GraphStore) -> None:
    """Registra `page_id` como pai candidato de cada link interno descoberto nele."""
    for child_id in links_filhos:
        if child_id != page_id:
            store.set_parent_if_absent(child_id, page_id)


def resolver_taxonomia(page_id: str, store: GraphStore, max_depth: int = 6) -> dict[str, str]:
    """
    Retorna {"sistema": ..., "modulo": ...} para `page_id`, subindo a cadeia
    de pais até achar um hub conhecido. Sem match, retorna os defaults.
    """
    if page_id in KNOWN_SYSTEM_HUBS:
        sistema, modulo = KNOWN_SYSTEM_HUBS[page_id]
        return {"sistema": sistema, "modulo": modulo}

    current = page_id
    for _ in range(max_depth):
        parent = store.get_parent(current)
        if not parent:
            break
        if parent in KNOWN_SYSTEM_HUBS:
            sistema, modulo = KNOWN_SYSTEM_HUBS[parent]
            return {"sistema": sistema, "modulo": modulo}
        current = parent

    return {"sistema": DEFAULT_SISTEMA, "modulo": DEFAULT_MODULO}
