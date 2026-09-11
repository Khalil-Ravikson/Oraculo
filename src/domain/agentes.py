"""
src/domain/agentes.py
=====================
Os domínios de conhecimento do Oráculo, como dado de domínio.

Substitui `agents/base.py` + `agents/registry.py` + `agents/bootstrap.py` e as
quatro classes `*Agent` que existiam só para satisfazê-los (2026-09-10).

## Por que a abstração saiu

O `AgentRegistry` prometia resolução dinâmica: "o roteador nunca importa uma
classe de agente, sempre resolve por nome". **Isso nunca chegou a acontecer.**
Nenhum consumidor do pipeline de mensagem chamava `resolve()` — cada nó do
grafo importa a classe de serviço do domínio direto e estaticamente. Os
únicos consumidores eram o painel `/hub/agents` e uma checagem de nome no
`route_registry`, e os dois queriam a mesma coisa: **a lista de nomes
válidos**.

Um `Protocol`, um registro em memória, um bootstrap assíncrono e quatro
classes adaptadoras para entregar uma lista de quatro strings é uma
indireção que só tinha custo. O custo era concreto: a arquitetura *parecia*
ter um framework multiagente plugável, e a documentação repetiu isso por
meses.

## O que "agente" significa aqui

Um **domínio de conhecimento** que pode ser ligado ou desligado inteiro pelo
painel (o circuit-breaker de `/hub/agents`, aplicado no `classify_node`).
Não é um processo autônomo, não decide chamadas de ferramenta em loop, e não
tem ciclo de vida próprio.

A verdade editável (descrição, provider e modelo por agente) vive na tabela
`agentes_catalogo`. Este módulo guarda só o conjunto fechado de nomes e a
descrição de partida, para o sistema subir com o banco vazio ou fora do ar.
"""
from __future__ import annotations

# Nome → descrição de partida. A descrição que o painel mostra vem de
# `agentes_catalogo`; esta é o fallback de primeira carga.
#
# Cada nome corresponde a um pacote de serviço, e a correspondência é
# convenção, não mecanismo — nada aqui importa aquele pacote:
#   academic_knowledge → src/rag/knowledge/
#   sigaa · tickets · conversation → src/domain_services/
AGENTES: dict[str, str] = {
    "academic_knowledge": (
        "Responde perguntas com base nos documentos da UEMA (busca + síntese). "
        "É o único agente no caminho crítico do v1."
    ),
    "sigaa": (
        "Consulta dados acadêmicos do discente no SIGAA. Fora do escopo do v1 — "
        "exige login por conversa."
    ),
    "conversation": "Saudação, boas-vindas e cadastro de novos usuários.",
    "tickets": (
        "Abertura de chamado e atualização de cadastro. Fora do escopo do v1 — "
        "não há integração real com o GLPI."
    ),
}

NOMES: frozenset[str] = frozenset(AGENTES)


def existe(nome: str) -> bool:
    """O agente existe? Substitui o `registry.resolve()` usado como checagem
    de existência — que levantava `KeyError` para dizer "não"."""
    return nome in AGENTES


def descricao(nome: str) -> str:
    return AGENTES.get(nome, "")
