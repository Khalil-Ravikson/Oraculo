"""Testes do motor de menu (item B2 do plano de recuperação).

O resolver é uma função pura de propósito — estes testes não sobem Redis,
Postgres nem LLM nenhum. Se um deles precisar de infra um dia, o desenho
regrediu.

O que estes testes protegem, em ordem de importância:
1. Navegar o menu NUNCA produz um resultado do tipo "pergunta" (é o que
   garante o custo zero em token).
2. O usuário nunca fica preso: `menu` e o pedido de atendente funcionam de
   qualquer tela, inclusive esperando uma pergunta.
3. A taxonomia (`rota`/`doc_type`) do RAG vem da tecla apertada, não de
   palpite sobre o texto.
"""
from __future__ import annotations

import pytest

from src.application.menu.loader import menu_default
from src.application.menu.resolver import resolver
from src.application.menu.spec import MenuConfig, render, validate_menu
from src.application.menu.state import MenuEstado


@pytest.fixture
def config() -> MenuConfig:
    return menu_default()


def _ate(config: MenuConfig, *mensagens: str):
    """Navega a partir de sessão nova e devolve o último resultado."""
    estado: MenuEstado | None = None
    resultado = None
    for msg in mensagens:
        resultado = resolver(config, estado, msg)
        estado = resultado.estado
    assert resultado is not None
    return resultado


# ── O menu embutido é válido ─────────────────────────────────────────────

def test_default_json_passa_na_validacao(config: MenuConfig) -> None:
    validate_menu(config)


def test_toda_tela_renderiza_com_rodape(config: MenuConfig) -> None:
    for no_id in config.nos:
        texto = render(config, no_id)
        assert texto
        assert config.rodape in texto


# ── Abertura ─────────────────────────────────────────────────────────────

def test_primeira_mensagem_abre_o_menu_principal(config: MenuConfig) -> None:
    r = resolver(config, None, "oi")
    assert r.tipo == "menu"
    assert r.estado.no == config.raiz
    assert "1  Senha, e-mail e Wi-Fi" in r.texto


def test_sessao_nova_com_pergunta_direta_tambem_abre_o_menu(config: MenuConfig) -> None:
    # Sem o menu, o usuário nunca descobre o que o bot faz.
    r = resolver(config, None, "como troco minha senha do sigaa?")
    assert r.tipo == "menu"


# ── Navegação: o invariante de custo ─────────────────────────────────────

@pytest.mark.parametrize("caminho", [
    ("oi", "1"),
    ("oi", "2"),
    ("oi", "3"),
    ("oi", "1", "9"),
    ("oi", "2", "9", "3"),
])
def test_navegar_nunca_dispara_pergunta(config: MenuConfig, caminho: tuple[str, ...]) -> None:
    """Nenhum caminho só de teclas de navegação pode acabar em LLM."""
    r = _ate(config, *caminho)
    assert r.tipo == "menu"
    assert r.rota == ""
    assert r.query == ""


def test_submenu_empilha_e_voltar_desempilha(config: MenuConfig) -> None:
    dentro = _ate(config, "oi", "2")
    assert dentro.estado.no == "sistemas"
    assert dentro.estado.pilha == ["principal"]

    fora = resolver(config, dentro.estado, "9")
    assert fora.estado.no == "principal"
    assert fora.estado.pilha == []


def test_menu_volta_a_raiz_de_qualquer_lugar(config: MenuConfig) -> None:
    dentro = _ate(config, "oi", "1")
    assert dentro.estado.no == "acesso"

    r = resolver(config, dentro.estado, "MENU")
    assert r.tipo == "menu"
    assert r.estado.no == config.raiz


# ── Texto fixo: zero token ───────────────────────────────────────────────

def test_texto_fixo_responde_sem_llm(config: MenuConfig) -> None:
    r = _ate(config, "oi", "1", "4")  # Wi-Fi
    assert r.tipo == "texto"
    assert r.texto
    assert r.rota == ""


def test_texto_fixo_mantem_o_usuario_na_mesma_tela(config: MenuConfig) -> None:
    r = _ate(config, "oi", "3", "1")  # contato da CTIC
    assert r.tipo == "texto"
    assert r.estado.no == "contatos"


# ── Perguntas: a taxonomia vem da tecla ──────────────────────────────────

def test_opcao_de_pergunta_pede_a_pergunta_antes_de_chamar_o_rag(config: MenuConfig) -> None:
    r = _ate(config, "oi", "2", "1")  # SIGAA
    assert r.tipo == "menu"           # ainda não é pergunta: só o convite
    assert r.estado.pendente is not None
    assert r.estado.pendente.rota == "WIKI"
    assert r.estado.pendente.filtros == {"sistema": "sigaa"}


def test_texto_apos_o_convite_vira_a_query_do_rag(config: MenuConfig) -> None:
    r = _ate(config, "oi", "2", "1", "como emito declaração de vínculo?")
    assert r.tipo == "pergunta"
    assert r.rota == "WIKI"
    assert r.doc_type == "wiki_ctic"
    assert r.filtros == {"sistema": "sigaa"}
    assert r.query == "como emito declaração de vínculo?"


def test_sipac_e_sigaa_usam_filtros_diferentes(config: MenuConfig) -> None:
    sigaa = _ate(config, "oi", "2", "1", "pergunta")
    sipac = _ate(config, "oi", "2", "2", "pergunta")
    assert sigaa.filtros == {"sistema": "sigaa"}
    assert sipac.filtros == {"sistema": "sipac"}


def test_contato_de_setor_usa_doc_type_contatos(config: MenuConfig) -> None:
    r = _ate(config, "oi", "3", "3", "telefone da biblioteca")
    assert r.tipo == "pergunta"
    assert r.rota == "CONTATOS"
    assert r.doc_type == "contatos"


def test_pergunta_pendente_aceita_texto_que_parece_numero(config: MenuConfig) -> None:
    # "2" logo depois do convite é a pergunta, não uma tecla — o bot acabou
    # de pedir uma pergunta.
    r = _ate(config, "oi", "2", "3", "bloco 2")
    assert r.tipo == "pergunta"
    assert r.query == "bloco 2"


def test_pergunta_pendente_nao_sobrevive_a_resposta(config: MenuConfig) -> None:
    r = _ate(config, "oi", "2", "1", "minha pergunta")
    assert r.estado.pendente is None


# ── Texto solto ──────────────────────────────────────────────────────────

def test_texto_solto_na_raiz_vira_pergunta_na_wiki(config: MenuConfig) -> None:
    na_raiz = _ate(config, "oi")
    r = resolver(config, na_raiz.estado, "o wifi da uema não conecta")
    assert r.tipo == "pergunta"
    assert r.rota == "WIKI"
    assert r.doc_type == "wiki_ctic"


def test_texto_nao_reconhecido_em_submenu_cai_no_fallback(config: MenuConfig) -> None:
    dentro = _ate(config, "oi", "1")
    r = resolver(config, dentro.estado, "sei lá")
    assert r.tipo == "texto"
    assert r.texto == config.fallback
    assert r.estado.no == "acesso"  # não perde o lugar


def test_tecla_inexistente_em_submenu_cai_no_fallback(config: MenuConfig) -> None:
    dentro = _ate(config, "oi", "3")
    r = resolver(config, dentro.estado, "7")
    assert r.tipo == "texto"
    assert r.texto == config.fallback


# ── Handoff: a saída de emergência ───────────────────────────────────────

def test_zero_pede_atendente(config: MenuConfig) -> None:
    r = _ate(config, "oi", "0")
    assert r.tipo == "handoff"


@pytest.mark.parametrize("frase", [
    "quero falar com um atendente",
    "me passa pra uma pessoa",
    "preciso de alguem",
    "FALAR COM ALGUÉM",
    "suporte",
])
def test_pedido_de_gente_funciona_em_qualquer_tela(config: MenuConfig, frase: str) -> None:
    dentro = _ate(config, "oi", "2")
    r = resolver(config, dentro.estado, frase)
    assert r.tipo == "handoff"


def test_pedido_de_gente_vence_pergunta_pendente(config: MenuConfig) -> None:
    """Se o bot pediu uma pergunta e o usuário desistiu, ele tem que
    conseguir sair — senão o convite vira uma armadilha."""
    convite = _ate(config, "oi", "2", "1")
    assert convite.estado.pendente is not None

    r = resolver(config, convite.estado, "quero falar com um atendente")
    assert r.tipo == "handoff"


def test_menu_vence_pergunta_pendente(config: MenuConfig) -> None:
    convite = _ate(config, "oi", "2", "1")
    r = resolver(config, convite.estado, "menu")
    assert r.tipo == "menu"
    assert r.estado.pendente is None


# ── Pós-resposta (B6) ────────────────────────────────────────────────────

def test_outra_pergunta_reaproveita_a_taxonomia_anterior(config: MenuConfig) -> None:
    """A tela "Isso ajudou? / 3 Fazer outra pergunta" não reclassifica nada:
    mantém o doc_type da resposta que acabou de sair."""
    respondida = _ate(config, "oi", "3", "3", "telefone da biblioteca")
    assert respondida.rota == "CONTATOS"

    pos = MenuEstado(
        no=config.pos_resposta,
        ultima_rota=respondida.estado.ultima_rota,
        ultimo_doc_type=respondida.estado.ultimo_doc_type,
    )
    convite = resolver(config, pos, "3")
    assert convite.estado.pendente is not None
    assert convite.estado.pendente.rota == "CONTATOS"
    assert convite.estado.pendente.doc_type == "contatos"


def test_nao_pos_resposta_vira_handoff(config: MenuConfig) -> None:
    pos = MenuEstado(no=config.pos_resposta)
    r = resolver(config, pos, "2")
    assert r.tipo == "handoff"


# ── Robustez ─────────────────────────────────────────────────────────────

def test_no_removido_da_config_devolve_para_a_raiz(config: MenuConfig) -> None:
    """Alguém editou o menu pelo painel e apagou a tela onde o usuário
    estava. Isso não pode virar um erro para ele."""
    orfao = MenuEstado(no="tela_que_nao_existe_mais")
    r = resolver(config, orfao, "1")
    assert r.tipo == "menu"
    assert r.estado.no == config.raiz


@pytest.mark.parametrize("texto", ["  1  ", "1", "\t1\n"])
def test_espacos_em_volta_da_tecla_nao_quebram(config: MenuConfig, texto: str) -> None:
    na_raiz = _ate(config, "oi")
    r = resolver(config, na_raiz.estado, texto)
    assert r.estado.no == "acesso"
