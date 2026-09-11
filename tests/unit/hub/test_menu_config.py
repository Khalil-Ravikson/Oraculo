"""Testes da edição do menu pelo painel (item C2.5).

Sem Postgres: o repositório é exercitado contra uma sessão dublê que registra
as operações. O que interessa aqui não é o SQL — é o contrato que protege o
usuário final:

1. Menu inválido **não grava nada**. A tela pode ter um bug; o bot não pode
   ficar com uma tela órfã, uma tecla duplicada ou uma opção sem destino,
   porque no WhatsApp não existe botão de voltar.
2. Concorrência otimista de verdade: salvar com versão velha levanta, em vez
   de sobrescrever o trabalho de outra pessoa.
3. A pré-visualização do painel usa a MESMA função que o bot — se as duas
   divergirem, o painel mente sobre o produto.
"""
from __future__ import annotations

import pytest

from src.application.menu.loader import menu_default
from src.application.menu.spec import MenuConfig, render, validate_menu


# ── O menu embutido é a base de tudo ─────────────────────────────────────

def test_default_valido():
    validate_menu(menu_default())


def test_default_sobrevive_a_ida_e_volta_por_json():
    """O painel manda o menu como JSON e o backend revalida. Se o
    `model_dump()` perdesse um campo, a volta seria inválida — e o operador
    veria um erro que ele não causou."""
    original = menu_default()
    devolvido = MenuConfig.model_validate(original.model_dump())
    validate_menu(devolvido)
    assert devolvido.model_dump() == original.model_dump()


# ── Validação: o que o painel NÃO pode gravar ────────────────────────────

def _menu_minimo() -> dict:
    return {
        "version": 1,
        "raiz": "principal",
        "nos": {
            "principal": {
                "titulo": "Principal",
                "abertura": "Olá",
                "opcoes": [
                    {"tecla": "1", "label": "Falar", "acao": "handoff"},
                ],
            }
        },
    }


def test_tela_sem_opcoes_e_rejeitada():
    bruto = _menu_minimo()
    bruto["nos"]["principal"]["opcoes"] = []
    with pytest.raises(ValueError, match="sem opções"):
        validate_menu(MenuConfig.model_validate(bruto))


def test_tecla_duplicada_e_rejeitada():
    bruto = _menu_minimo()
    bruto["nos"]["principal"]["opcoes"].append(
        {"tecla": "1", "label": "Outra", "acao": "handoff"}
    )
    with pytest.raises(ValueError, match="duplicadas"):
        validate_menu(MenuConfig.model_validate(bruto))


def test_submenu_para_tela_inexistente_e_rejeitado():
    bruto = _menu_minimo()
    bruto["nos"]["principal"]["opcoes"] = [
        {"tecla": "1", "label": "Ir", "acao": "submenu", "destino": "nao_existe"}
    ]
    with pytest.raises(ValueError, match="não existe"):
        validate_menu(MenuConfig.model_validate(bruto))


def test_tela_orfa_e_rejeitada():
    """Uma tela que nenhuma opção alcança é conteúdo que ninguém vai ler."""
    bruto = _menu_minimo()
    bruto["nos"]["perdida"] = {
        "titulo": "Perdida", "abertura": "",
        "opcoes": [{"tecla": "1", "label": "x", "acao": "handoff"}],
    }
    with pytest.raises(ValueError, match="inalcançáveis"):
        validate_menu(MenuConfig.model_validate(bruto))


def test_pergunta_sem_convite_e_rejeitada():
    """Sem o convite, o bot pediria uma pergunta sem dizer nada."""
    bruto = _menu_minimo()
    bruto["nos"]["principal"]["opcoes"] = [
        {"tecla": "1", "label": "Dúvida", "acao": "pergunta",
         "rota": "WIKI", "doc_type": "wiki_ctic", "prompt": "   "}
    ]
    with pytest.raises(ValueError, match="sem prompt"):
        validate_menu(MenuConfig.model_validate(bruto))


def test_resposta_pronta_vazia_e_rejeitada():
    bruto = _menu_minimo()
    bruto["nos"]["principal"]["opcoes"] = [
        {"tecla": "1", "label": "Senha", "acao": "texto_fixo", "texto": "  "}
    ]
    with pytest.raises(ValueError, match="sem texto"):
        validate_menu(MenuConfig.model_validate(bruto))


def test_raiz_precisa_existir():
    bruto = _menu_minimo()
    bruto["raiz"] = "tela_que_nao_existe"
    with pytest.raises(ValueError, match="raiz"):
        validate_menu(MenuConfig.model_validate(bruto))


# ── A pré-visualização é a mesma coisa que o bot manda ───────────────────

def test_preview_do_painel_e_a_mesma_render_do_bot():
    config = menu_default()
    texto = render(config, config.raiz)

    # Formato: abertura, linha em branco, opções numeradas, rodapé.
    assert "1  Senha, e-mail e Wi-Fi" in texto
    assert config.rodape in texto
    # A ordem das opções na tela é a ordem da lista — não alfabética, não por
    # tecla: quem edita decide o que vem primeiro.
    pos = [texto.index(f"{o.tecla}  {o.label}") for o in config.nos[config.raiz].opcoes]
    assert pos == sorted(pos)


def test_render_de_tela_inexistente_levanta():
    with pytest.raises(KeyError):
        render(menu_default(), "nao_existe")


# ── Repositório: concorrência ────────────────────────────────────────────

class _SessaoFake:
    """Dublê mínimo de `AsyncSession` — registra o que foi executado."""

    def __init__(self, linha_atual=None):
        self.linha_atual = linha_atual
        self.executados: list[str] = []

    async def execute(self, stmt):
        self.executados.append(type(stmt).__name__)
        return _ResultadoFake(self)

    async def flush(self):
        pass


class _ResultadoFake:
    def __init__(self, sessao):
        self._sessao = sessao
        self.rowcount = 1

    def scalar_one_or_none(self):
        return self._sessao.linha_atual

    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _LinhaFake:
    def __init__(self, config, versao):
        self.config = config
        self.versao = versao
        self.tenant_id = None
        self.atualizado_em = None
        self.atualizado_por = "alguem"


@pytest.mark.asyncio
async def test_salvar_com_versao_velha_levanta_conflito():
    from src.infrastructure.repositories.menu_config_repository import (
        ConflitoDeVersao, MenuConfigRepository,
    )

    atual = _LinhaFake({"version": 1}, versao=7)
    repo = MenuConfigRepository(_SessaoFake(linha_atual=atual))

    with pytest.raises(ConflitoDeVersao):
        # A tela achava que estava na versão 3; o banco já está na 7.
        await repo.salvar({"version": 1}, versao_esperada=3)


@pytest.mark.asyncio
async def test_primeira_gravacao_nasce_na_versao_1():
    from src.infrastructure.repositories.menu_config_repository import MenuConfigRepository

    repo = MenuConfigRepository(_SessaoFake(linha_atual=None))
    r = await repo.salvar({"version": 1}, versao_esperada=0)

    assert r["versao"] == 1


@pytest.mark.asyncio
async def test_salvar_com_a_versao_certa_incrementa():
    from src.infrastructure.repositories.menu_config_repository import MenuConfigRepository

    atual = _LinhaFake({"version": 1}, versao=4)
    repo = MenuConfigRepository(_SessaoFake(linha_atual=atual))
    r = await repo.salvar({"version": 1}, versao_esperada=4)

    assert r["versao"] == 5


# ── Loader: a cadeia de degradação ───────────────────────────────────────

@pytest.mark.asyncio
async def test_loader_cai_no_menu_embutido_sem_infra(monkeypatch):
    """Sem Redis e sem Postgres, o bot continua com menu. Um bot mudo é pior
    que um menu velho."""
    import src.application.menu.loader as loader

    def explode():
        raise RuntimeError("sem redis")

    async def explode_async():
        raise RuntimeError("sem postgres")

    monkeypatch.setattr(loader, "_do_redis", explode)
    monkeypatch.setattr(loader, "_do_postgres", explode_async)

    config = await loader.carregar_menu()
    assert config.model_dump() == menu_default().model_dump()


@pytest.mark.asyncio
async def test_loader_prefere_o_postgres_ao_embutido(monkeypatch):
    import src.application.menu.loader as loader

    editado = menu_default().model_copy(deep=True)
    editado.nos[editado.raiz].abertura = "Texto editado pelo painel"

    monkeypatch.setattr(loader, "_do_redis", lambda: None)

    async def do_pg():
        return editado

    monkeypatch.setattr(loader, "_do_postgres", do_pg)
    monkeypatch.setattr(loader, "_aquecer_redis", lambda c: None)

    config = await loader.carregar_menu()
    assert config.nos[config.raiz].abertura == "Texto editado pelo painel"
