"""Taxonomia da wiki da CTIC: normalização de page_id e mapa de hubs.

Dois achados de 2026-09-11, ligados entre si.

**O primeiro é de acento, o terceiro desta classe na mesma rodada.**
`_normalize_page_id` baixava a caixa e trocava espaço por `_`, mas não
removia acento — a DokuWiki remove. Um link `[[Catálogo de Materiais]]`
virava o id `catálogo_de_materiais`, enquanto a página real é
`catalogo_de_materiais`. O grafo de pais era gravado numa chave inexistente,
e `resolver_taxonomia()` devolvia "Geral".

Dos 8 módulos do SIPAC, só `almoxarifado`, `contratos` e `protocolo` não têm
acento. Eram exatamente os três que funcionavam.

**O segundo é de cobertura.** O mapa curado tinha 10 hubs, todos de SIPAC e
SIGUEMA. A wiki tem sete áreas de topo, incluindo SIGRH (servidores), Office
e LibreOffice — nenhuma delas mapeada.
"""
from __future__ import annotations

import pytest

from src.infrastructure.scraping.implementations.dokuwiki.hierarchy import (
    KNOWN_SYSTEM_HUBS,
)
from src.infrastructure.scraping.implementations.dokuwiki.wikitext import (
    _normalize_page_id,
)


# ── Normalização de page_id ──────────────────────────────────────────────

@pytest.mark.parametrize("rotulo,esperado", [
    ("Catálogo de Materiais",      "catalogo_de_materiais"),
    ("Compras e Licitações",       "compras_e_licitacoes"),
    ("Orçamento",                  "orcamento"),
    ("Patrimônio Móvel",           "patrimonio_movel"),
    ("Atendimento de Requisições", "atendimento_de_requisicoes"),
    ("Frequência",                 "frequencia"),
    ("Administração de Pessoal",   "administracao_de_pessoal"),
    ("Acadêmico",                  "academico"),
])
def test_acento_e_removido_como_a_dokuwiki_faz(rotulo, esperado):
    """Cada um destes é um módulo real da wiki. Com acento no id, o hub nunca
    casa e a taxonomia do trecho se perde."""
    assert _normalize_page_id(rotulo) == esperado


def test_espaco_vira_sublinhado_e_caixa_baixa():
    assert _normalize_page_id("  Portal DO Servidor ") == "portal_do_servidor"


def test_id_ja_normalizado_nao_muda():
    assert _normalize_page_id("almoxarifado") == "almoxarifado"


# ── Mapa de hubs ─────────────────────────────────────────────────────────

def test_nenhuma_chave_do_mapa_tem_acento():
    """Chave acentuada é chave morta: nunca casa, e não dá erro — só devolve
    "Geral" em silêncio. É o defeito que este mapa existe para corrigir."""
    acentos = "áàâãäéèêëíìîïóòôõöúùûüçÁÉÍÓÚÇ"
    for chave in KNOWN_SYSTEM_HUBS:
        assert not any(c in chave for c in acentos), chave


def test_as_chaves_sao_id_valido_de_dokuwiki():
    """Mesma normalização que a extração de link aplica — se divergirem, o
    hub não casa."""
    for chave in KNOWN_SYSTEM_HUBS:
        assert _normalize_page_id(chave) == chave, chave


@pytest.mark.parametrize("sistema", ["SIPAC", "SIGAA", "SIGRH", "Office", "LibreOffice"])
def test_os_cinco_sistemas_da_wiki_estao_mapeados(sistema):
    """A wiki tem sete áreas de topo. Antes desta rodada o mapa só conhecia
    SIPAC e SIGUEMA — SIGRH (servidores), Office e LibreOffice não tinham
    taxonomia nenhuma, e o menu não tem como filtrar o que não está marcado."""
    assert any(s == sistema for s, _ in KNOWN_SYSTEM_HUBS.values()), sistema


@pytest.mark.parametrize("hub", [
    "modulos-sipac", "modulos-sigaa", "modulos-sigrh",
    "ferramentas-office", "libre-office", "tira-duvida",
])
def test_as_paginas_de_topo_da_wiki_estao_no_mapa(hub):
    """Estes seis são os hubs que a própria página `start` da wiki lista."""
    assert hub in KNOWN_SYSTEM_HUBS


def test_todo_hub_tem_sistema_e_modulo_preenchidos():
    for chave, valor in KNOWN_SYSTEM_HUBS.items():
        sistema, modulo = valor
        assert sistema and modulo, chave
