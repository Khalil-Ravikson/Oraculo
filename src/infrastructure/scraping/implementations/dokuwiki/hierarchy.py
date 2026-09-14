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


# page_id de hub → (sistema, modulo).
#
# Levantado em 2026-09-11 a partir da PRÓPRIA estrutura da wiki: a página
# `start` lista as áreas de topo, e cada uma lista seus módulos. Não é
# adivinhação por nome de página — é a organização que a CTIC já usa.
#
# As sete áreas de topo, e quantas páginas cada uma cobre das 1383:
#
#   modulos-sipac        administrativo (compras, patrimônio, protocolo…)
#   modulos-sigaa        acadêmico
#   modulos-sigrh        recursos humanos  ← NÃO estava mapeado
#   ferramentas-office   Microsoft         ← NÃO estava mapeado
#   libre-office         LibreOffice       ← NÃO estava mapeado
#   tira-duvida          dúvidas frequentes
#   arquivos-importantes anexos
#
# ATENÇÃO ao acento: os page_id reais da DokuWiki são SEM acento
# (`catalogo_de_materiais`, não `catálogo_de_materiais`) — conferido contra a
# lista completa de `?do=index`. Escrever a chave com acento aqui faz o hub
# nunca casar, e o trecho cai em "Geral" em silêncio. Foi o que aconteceu até
# `wikitext._normalize_page_id` passar a remover acentos.
# ATENÇÃO: este dict NÃO é mais lido em produção desde 2026-09-14 — vira
# referência histórica, mantida só para quem quiser repopular
# `wiki_taxonomia` a partir daqui manualmente. A taxonomia ativa vem de
# `taxonomia_store.listar_hubs_dict()` e é passada explicitamente a
# `resolver_taxonomia(..., hubs=...)`.
KNOWN_SYSTEM_HUBS: dict[str, tuple[str, str]] = {
    # ── SIPAC — administrativo ────────────────────────────────────────────
    "modulos-sipac": ("SIPAC", "Geral"),
    "almoxarifado": ("SIPAC", "Almoxarifado"),
    "catalogo_de_materiais": ("SIPAC", "Catalogo de Materiais"),
    "compras_e_licitacoes": ("SIPAC", "Compras e Licitacoes"),
    "contratos": ("SIPAC", "Contratos"),
    "orcamento": ("SIPAC", "Orcamento"),
    "patrimonio_movel": ("SIPAC", "Patrimonio Movel"),
    "protocolo": ("SIPAC", "Protocolo"),
    "atendimento_de_requisicoes": ("SIPAC", "Atendimento de Requisicoes"),

    # ── SIGAA — acadêmico ─────────────────────────────────────────────────
    "modulos-sigaa": ("SIGAA", "Geral"),
    "academico": ("SIGAA", "Academico"),
    "ouvidoria": ("SIGAA", "Ouvidoria"),
    "caixa_postal": ("SIGAA", "Caixa Postal"),
    "graduacao": ("SIGAA", "Graduacao"),
    "stricto_sensu": ("SIGAA", "Pos-Graduacao"),
    "lato_sensu": ("SIGAA", "Pos-Graduacao"),
    "extensao": ("SIGAA", "Extensao"),
    "matricula_online": ("SIGAA", "Matricula"),

    # ── SIGRH — recursos humanos (servidores) ─────────────────────────────
    "modulos-sigrh": ("SIGRH", "Geral"),
    "administracao_de_pessoal": ("SIGRH", "Administracao de Pessoal"),
    "cadastro": ("SIGRH", "Cadastro"),
    "consultas_funcionais": ("SIGRH", "Consultas Funcionais"),
    "frequencia": ("SIGRH", "Frequencia"),
    "portal_do_servidor": ("SIGRH", "Portal do Servidor"),
    "portal_da_chefia_da_unidade": ("SIGRH", "Portal da Chefia"),

    # ── Ferramentas de escritório ─────────────────────────────────────────
    "ferramentas-office": ("Office", "Geral"),
    "teams": ("Office", "Teams"),
    "whiteboard": ("Office", "Whiteboard"),
    "forms": ("Office", "Forms"),
    "onenote": ("Office", "OneNote"),
    "word": ("Office", "Word"),
    "excel": ("Office", "Excel"),
    "powerpoint": ("Office", "PowerPoint"),
    "sharepoint": ("Office", "SharePoint"),

    "libre-office": ("LibreOffice", "Geral"),
    "writer": ("LibreOffice", "Writer"),
    "calc": ("LibreOffice", "Calc"),
    "impress": ("LibreOffice", "Impress"),

    # ── Dúvidas frequentes ────────────────────────────────────────────────
    "tira-duvida": ("Duvidas", "Geral"),
    "siguema_academico": ("SIGAA", "Duvidas"),
    "e-mail_institucional": ("Acesso", "E-mail"),
    "portais_siguema_discente_docente_e_coord._de_curso": ("SIGAA", "Portais"),
    "cadastro_de_discente_no_siguema": ("SIGAA", "Cadastro de Discente"),

    # Todas as chaves acima foram conferidas contra a lista completa de
    # `?do=index`: hub que não existe como página é chave morta, e chave morta
    # aqui não dá erro — só devolve "Geral" em silêncio, que é o defeito que
    # este mapa existe para corrigir.
    #
    # Saíram da curadoria por não existirem como página: `siguema`,
    # `siguema_administrativo`, `siguema_rh` e `microsoft_teams` — apareciam
    # como texto de link em `tira-duvida`, sem página correspondente.
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


def resolver_taxonomia(
    page_id: str, store: GraphStore, max_depth: int = 6,
    hubs: dict[str, tuple[str, str]] | None = None,
) -> dict[str, str]:
    """
    Retorna {"sistema": ..., "modulo": ...} para `page_id`, subindo a cadeia
    de pais até achar um hub conhecido. Sem match, retorna os defaults.

    `hubs` é a taxonomia ATIVA (hoje vem de `wiki_taxonomia`/`taxonomia_store`,
    carregada uma vez por rodada de ingestão — ver `scraper.py`). Sem `hubs`
    (None), não usa `KNOWN_SYSTEM_HUBS` como fallback — a taxonomia foi
    zerada de propósito (decisão do dono, 2026-09-14): fica tudo em "Geral"
    até ser reclassificado pelo painel `/hub/wiki/taxonomia`.
    """
    hubs = hubs or {}

    if page_id in hubs:
        sistema, modulo = hubs[page_id]
        return {"sistema": sistema, "modulo": modulo}

    current = page_id
    for _ in range(max_depth):
        parent = store.get_parent(current)
        if not parent:
            break
        if parent in hubs:
            sistema, modulo = hubs[parent]
            return {"sistema": sistema, "modulo": modulo}
        current = parent

    return {"sistema": DEFAULT_SISTEMA, "modulo": DEFAULT_MODULO}
