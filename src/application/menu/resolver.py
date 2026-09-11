"""
src/application/menu/resolver.py
===============================
O roteador do v1. Substitui a camada L5 do Supervisor (o Gemini Flash) por
uma função pura, determinística e de custo zero.

Contrato: entra `(config, estado, texto)`, sai `MenuResultado`. Sem Redis, sem
Postgres, sem rede, sem LLM — é por isso que dá para testar o bot inteiro sem
subir infraestrutura, e é por isso que navegar o menu não gasta token.

Quem persiste o estado devolvido é o chamador (`entrypoint.py`), não este
módulo. Mesma divisão que `redis_state.py` faz com `auth_flow.py`.

## Precedência das regras

A ordem abaixo é a decisão de desenho mais importante do arquivo, porque é o
que garante que o usuário nunca fica preso:

1. **Atalhos globais** (`menu`, `início`, pedido explícito de atendente) —
   valem em QUALQUER tela, inclusive esperando uma pergunta. Um usuário
   travado escreve "menu" ou "quero falar com alguém", e isso tem que
   funcionar mesmo que ele esteja no meio de outra coisa.
2. **Pergunta pendente** — se o bot pediu uma pergunta, o texto livre É a
   pergunta. Só perde para os atalhos acima.
3. **Tecla do menu atual.**
4. **Texto livre** — na raiz vira pergunta na wiki; num submenu vira o
   fallback ("não entendi"), porque ali o número é a única resposta que faz
   sentido.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from src.application.menu.spec import MenuConfig, MenuOpcao, render
from src.application.menu.state import MenuEstado, PerguntaPendente

# Tipos de resultado, e o que o chamador faz com cada um:
#   menu      → responde `texto`, não invoca o grafo          (0 tokens)
#   texto     → responde `texto`, não invoca o grafo          (0 tokens)
#   handoff   → rota ESCALAR_HUMANO no grafo                  (0 tokens)
#   pergunta  → rota `rota` no grafo, com a query já em mãos  (1 síntese)
TipoResultado = Literal["menu", "texto", "handoff", "pergunta"]


@dataclass
class MenuResultado:
    tipo: TipoResultado
    estado: MenuEstado
    texto: str = ""
    # Só para tipo="pergunta": o que mandar ao RAG e com qual taxonomia.
    rota: str = ""
    doc_type: str = ""
    query: str = ""
    filtros: dict[str, str] = field(default_factory=dict)


# Um pedido de gente. Fica em regex e não em LLM de propósito: é a única
# saída de emergência do usuário, então não pode depender de cota de API,
# latência de provider nem de acerto de classificador.
_RE_ATENDENTE = re.compile(
    r"\b(atendente|humano|pessoa|alguem|suporte|funcionario|"
    r"falar\s+com\s+(alguem|voces|a\s+ctic))\b"
)
_RE_MENU = re.compile(r"^(menu|inicio|comecar|voltar\s+ao\s+inicio)$")
_RE_SAUDACAO = re.compile(r"^(oi|ola|opa|bom\s+dia|boa\s+tarde|boa\s+noite|eae|e\s+ai)[!.]*$")


def _normalizar(texto: str) -> str:
    """Minúsculas sem acento. Mensagem de WhatsApp vem como vem — "OLÁ",
    "Olá!", "ola" e "OLA" são a mesma intenção."""
    sem_acento = unicodedata.normalize("NFKD", texto.strip().lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def resolver(config: MenuConfig, estado: MenuEstado | None, texto: str) -> MenuResultado:
    """Decide o que o bot faz com esta mensagem. Ver a precedência no topo."""
    normalizado = _normalizar(texto)

    # ── 1. Atalhos globais ────────────────────────────────────────────────
    if _RE_ATENDENTE.search(normalizado):
        return _handoff(config, estado)

    if _RE_MENU.match(normalizado) or (estado is None and _RE_SAUDACAO.match(normalizado)):
        return _abrir_raiz(config, estado)

    # Sessão nova (ou expirada): qualquer coisa abre o menu principal. É o
    # "Dispara na 1ª mensagem" da especificação — inclusive quando a primeira
    # mensagem já é uma pergunta, porque sem o menu o usuário não descobre o
    # que o bot sabe fazer.
    if estado is None or not estado.no:
        return _abrir_raiz(config, estado)

    # ── 2. Pergunta pendente ──────────────────────────────────────────────
    if estado.pendente is not None:
        pendente = estado.pendente
        # Um número aqui é ambíguo ("2" pode ser a pergunta ou uma tecla).
        # Resolvido a favor da pergunta: o bot ACABOU de pedir uma pergunta,
        # e a saída global (menu / atendente) já foi testada acima.
        novo = MenuEstado(
            no=pendente.origem or estado.no,
            pilha=list(estado.pilha),
            ultima_rota=pendente.rota,
            ultimo_doc_type=pendente.doc_type,
        )
        return MenuResultado(
            tipo="pergunta",
            estado=novo,
            rota=pendente.rota,
            doc_type=pendente.doc_type,
            query=texto.strip(),
            filtros=dict(pendente.filtros),
        )

    # ── 3. Tecla do menu atual ────────────────────────────────────────────
    no = config.no(estado.no)
    if no is None:
        # O nó sumiu do menu entre uma mensagem e outra (alguém editou a
        # config pelo painel). Não é erro do usuário — devolve para a raiz.
        return _abrir_raiz(config, estado)

    opcao = no.opcao(normalizado)
    if opcao is not None:
        return _aplicar(config, estado, opcao)

    # ── 4. Texto livre ────────────────────────────────────────────────────
    if estado.no == config.raiz:
        # Na raiz, texto solto é uma pergunta de verdade — o usuário digitou
        # o que queria em vez de procurar o número. Vai para a wiki.
        return _pergunta_livre(config, estado, texto)

    return MenuResultado(tipo="texto", estado=estado, texto=config.fallback)


def _abrir_raiz(config: MenuConfig, estado: MenuEstado | None) -> MenuResultado:
    novo = MenuEstado(
        no=config.raiz,
        ultima_rota=estado.ultima_rota if estado else "",
        ultimo_doc_type=estado.ultimo_doc_type if estado else "",
    )
    return MenuResultado(tipo="menu", estado=novo, texto=render(config, config.raiz))


def _handoff(config: MenuConfig, estado: MenuEstado | None) -> MenuResultado:
    # A posição some: quando a equipe humana assume, o bot está silenciado
    # para a sessão (`handoff:session:*`) e não faz sentido guardar onde ele
    # estava no menu. Ao voltar, o usuário recomeça pela raiz.
    novo = MenuEstado(no=config.raiz)
    return MenuResultado(tipo="handoff", estado=novo)


def _aplicar(config: MenuConfig, estado: MenuEstado, opcao: MenuOpcao) -> MenuResultado:
    if opcao.acao == "handoff":
        return _handoff(config, estado)

    if opcao.acao == "submenu":
        novo = estado.empilhar(opcao.destino)
        return MenuResultado(tipo="menu", estado=novo, texto=render(config, opcao.destino))

    if opcao.acao == "voltar":
        novo = (
            MenuEstado(
                no=opcao.destino,
                ultima_rota=estado.ultima_rota,
                ultimo_doc_type=estado.ultimo_doc_type,
            )
            if opcao.destino
            else estado.desempilhar(config.raiz)
        )
        return MenuResultado(tipo="menu", estado=novo, texto=render(config, novo.no))

    if opcao.acao == "texto_fixo":
        corpo = opcao.texto.strip()
        if opcao.fecho.strip():
            corpo = f"{corpo}\n\n{opcao.fecho.strip()}"
        return MenuResultado(tipo="texto", estado=estado, texto=corpo)

    # acao == "pergunta": não responde nada ainda — pede a pergunta e anota
    # a taxonomia. É o único caminho que leva a uma chamada de LLM, e ele
    # sempre passa por esta confirmação explícita do usuário.
    #
    # "Fazer outra pergunta" na tela pós-resposta reaproveita a taxonomia da
    # resposta anterior quando existe, em vez da declarada na opção — é o
    # "mantém o mesmo doc_type, não reclassifica nada" da especificação.
    rota = opcao.rota
    doc_type = opcao.doc_type
    if estado.no == config.pos_resposta and estado.ultima_rota:
        rota = estado.ultima_rota
        doc_type = estado.ultimo_doc_type or doc_type

    novo = MenuEstado(
        no=estado.no,
        pilha=list(estado.pilha),
        pendente=PerguntaPendente(
            rota=rota,
            doc_type=doc_type,
            filtros=dict(opcao.filtros),
            origem=estado.no,
        ),
        ultima_rota=estado.ultima_rota,
        ultimo_doc_type=estado.ultimo_doc_type,
    )
    return MenuResultado(tipo="menu", estado=novo, texto=opcao.prompt.strip())


def _pergunta_livre(config: MenuConfig, estado: MenuEstado, texto: str) -> MenuResultado:
    """Texto solto na raiz. Vai direto para a wiki, sem pedir confirmação —
    o usuário já escreveu a pergunta, pedir para escrever de novo é atrito
    puro."""
    novo = MenuEstado(
        no=estado.no,
        pilha=list(estado.pilha),
        ultima_rota="WIKI",
        ultimo_doc_type="wiki_ctic",
    )
    return MenuResultado(
        tipo="pergunta",
        estado=novo,
        rota="WIKI",
        doc_type="wiki_ctic",
        query=texto.strip(),
    )
