"""
src/application/menu/spec.py
============================
O menu do bot como DADO, não como código.

Mesmo padrão da `GraphSpec` (ADR 0008 Fase 5): um documento versionado,
validado na escrita, resolvido em runtime na ordem
**Redis → Postgres (`menu_config`) → `menus/default.json` embutido**. O
`default.json` é o menu do v1; o painel (`/hub/menu`, item C2.5) edita a
versão em Postgres sem deploy.

Por que dado e não código: o texto de uma tela de menu muda por razão de
negócio (a CTIC trocou de ramal, o nome do sistema mudou), não de
engenharia. Um deploy por vírgula é o tipo de acoplamento que o v1 existe
para eliminar.

O que NÃO fica aqui: qualquer decisão sobre a mensagem do usuário. Isso é
`resolver.py`, que é uma função pura. Este módulo só descreve e valida a
topologia do menu, e renderiza o texto de uma tela.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Ações que uma opção de menu pode ter. Cada uma tem um custo de LLM
# conhecido e fixo — é essa tabela que garante que navegar o menu é grátis:
#   submenu     → 0 tokens (renderiza outra tela)
#   texto_fixo  → 0 tokens (imprime um texto do banco)
#   handoff     → 0 tokens (silencia o bot e chama a equipe)
#   voltar      → 0 tokens (desempilha um nível)
#   pergunta    → 1 chamada de síntese, cacheável (o ÚNICO caminho com LLM)
AcaoMenu = Literal["submenu", "texto_fixo", "pergunta", "handoff", "voltar"]


class MenuOpcao(BaseModel):
    """Uma linha numerada de uma tela de menu."""

    tecla: str
    label: str
    acao: AcaoMenu

    # acao="submenu" | "voltar": id do nó de destino. Em "voltar" é opcional —
    # vazio significa "desempilha", que é o comportamento normal do 9.
    destino: str = ""

    # acao="texto_fixo": o que o bot responde. `fecho` é a pergunta de
    # acompanhamento ("Conseguiu? 1 Sim · 2 Não") — opcional porque nem toda
    # resposta fixa precisa de follow-up.
    texto: str = ""
    fecho: str = ""

    # acao="pergunta": para onde a pergunta do usuário vai.
    # `rota` tem que existir no `route_registry`; `doc_type` é a taxonomia do
    # índice RAG (atenção: a rota WIKI usa "wiki_ctic", não "wiki").
    # `prompt` é o convite ("Escreva sua pergunta sobre o SIGAA. Ex.: ...").
    # `filtros` pré-preenche campos do índice (ex.: {"sistema": "sigaa"}) —
    # busca mais precisa e mais barata que perguntar sem filtro.
    rota: str = ""
    doc_type: str = ""
    prompt: str = ""
    filtros: dict[str, str] = Field(default_factory=dict)

    @field_validator("tecla")
    @classmethod
    def _tecla_curta(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 2:
            raise ValueError(f"tecla inválida: {v!r} (esperado 1-2 caracteres)")
        return v


class MenuNo(BaseModel):
    """Uma tela de menu: um texto de abertura e uma lista de opções."""

    titulo: str = ""
    # Texto que aparece ANTES da lista de opções. O corpo da tela (as linhas
    # numeradas) é RENDERIZADO das opções, nunca digitado à mão — assim o que
    # o usuário lê e o que o resolver aceita não podem divergir.
    abertura: str = ""
    opcoes: list[MenuOpcao] = Field(default_factory=list)

    def opcao(self, tecla: str) -> MenuOpcao | None:
        alvo = tecla.strip()
        return next((o for o in self.opcoes if o.tecla == alvo), None)


class MenuConfig(BaseModel):
    """O menu inteiro, versionado."""

    version: int = 1
    raiz: str = "principal"
    # Repetido no rodapé de toda tela — o usuário nunca precisa lembrar como
    # sair de onde está.
    rodape: str = "Digite o número. Para voltar ao início, escreva menu."
    # Resposta a um texto não reconhecido DENTRO de um submenu.
    fallback: str = (
        "Não entendi. Digite o número da opção, ou 0 para falar com uma pessoa."
    )
    # Tela mostrada DEPOIS de toda resposta de RAG ("Isso ajudou? 1 Sim ..."),
    # item B6. Não é alcançável por nenhuma opção — quem a dispara é o fim de
    # uma resposta, não uma tecla — então entra como raiz extra na checagem de
    # alcançabilidade em vez de ser tratada como órfã.
    pos_resposta: str = ""
    nos: dict[str, MenuNo] = Field(default_factory=dict)

    def no(self, no_id: str) -> MenuNo | None:
        return self.nos.get(no_id)


def render(config: MenuConfig, no_id: str) -> str:
    """Texto completo de uma tela: abertura + opções numeradas + rodapé.

    Renderizar em vez de guardar o texto pronto é o que impede a divergência
    clássica de bot de menu: a tela oferece uma opção 4 que o roteador não
    conhece, ou o roteador aceita uma 5 que a tela não mostra."""
    no = config.no(no_id)
    if no is None:
        raise KeyError(f"nó de menu inexistente: {no_id!r}")

    linhas: list[str] = []
    if no.abertura:
        linhas.append(no.abertura.strip())
        linhas.append("")

    for opcao in no.opcoes:
        linhas.append(f"{opcao.tecla}  {opcao.label}")

    if config.rodape:
        linhas.append("")
        linhas.append(config.rodape)

    return "\n".join(linhas).strip()


def validate_menu(config: MenuConfig) -> None:
    """Rejeita um menu quebrado ANTES de gravar — mesmo contrato do
    `validate_topology()` da GraphSpec.

    O que é erro aqui é sempre algo que viraria um beco sem saída para o
    usuário no WhatsApp, onde ele não tem botão de voltar."""
    if not config.nos:
        raise ValueError("menu sem nenhum nó")

    if config.raiz not in config.nos:
        raise ValueError(f"raiz {config.raiz!r} não está em nos")

    if config.pos_resposta and config.pos_resposta not in config.nos:
        raise ValueError(f"pos_resposta {config.pos_resposta!r} não está em nos")

    for no_id, no in config.nos.items():
        if not no.opcoes:
            raise ValueError(f"nó {no_id!r} sem opções — o usuário ficaria preso")

        teclas = [o.tecla for o in no.opcoes]
        duplicadas = {t for t in teclas if teclas.count(t) > 1}
        if duplicadas:
            raise ValueError(f"nó {no_id!r} tem teclas duplicadas: {sorted(duplicadas)}")

        for opcao in no.opcoes:
            _validar_opcao(config, no_id, opcao)

    _validar_alcancavel(config)


def _validar_opcao(config: MenuConfig, no_id: str, opcao: MenuOpcao) -> None:
    onde = f"nó {no_id!r}, tecla {opcao.tecla!r}"

    if opcao.acao == "submenu":
        if not opcao.destino:
            raise ValueError(f"{onde}: submenu sem destino")
        if opcao.destino not in config.nos:
            raise ValueError(f"{onde}: destino {opcao.destino!r} não existe")

    elif opcao.acao == "voltar":
        if opcao.destino and opcao.destino not in config.nos:
            raise ValueError(f"{onde}: destino {opcao.destino!r} não existe")

    elif opcao.acao == "texto_fixo":
        if not opcao.texto.strip():
            raise ValueError(f"{onde}: texto_fixo sem texto")

    elif opcao.acao == "pergunta":
        if not opcao.rota:
            raise ValueError(f"{onde}: pergunta sem rota")
        if not opcao.doc_type:
            raise ValueError(f"{onde}: pergunta sem doc_type")
        if not opcao.prompt.strip():
            raise ValueError(
                f"{onde}: pergunta sem prompt — o usuário não saberia o que escrever"
            )


def _validar_alcancavel(config: MenuConfig) -> None:
    """Todo nó tem que ser alcançável a partir da raiz.

    Um nó órfão é conteúdo que alguém escreveu e que ninguém nunca vai ler —
    o equivalente, no menu, do nó inalcançável que `validate_topology()`
    rejeita no grafo."""
    vistos: set[str] = set()
    fila = [config.raiz]
    if config.pos_resposta:
        fila.append(config.pos_resposta)

    while fila:
        atual = fila.pop()
        if atual in vistos:
            continue
        vistos.add(atual)
        no = config.no(atual)
        if no is None:
            continue
        for opcao in no.opcoes:
            if opcao.destino:
                fila.append(opcao.destino)

    orfaos = sorted(set(config.nos) - vistos)
    if orfaos:
        raise ValueError(f"nós inalcançáveis a partir da raiz: {orfaos}")
