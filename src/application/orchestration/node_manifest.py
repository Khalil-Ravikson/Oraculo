"""
src/application/orchestration/node_manifest.py
=============================================
Catálogo dos *tipos de nó* do grafo de orquestração — a paleta de peças que
uma `GraphSpec` pode instanciar (ADR 0008 Fase 5).

Cada nó de `nodes.py` é uma função `async def f(state) -> dict`. Aqui cada
função ganha metadados (rótulo humano, descrição, categoria, ícone, chaves do
state que lê/escreve) pro Hub renderizar o grafo e pro `builder` montá-lo a
partir da spec. Não é um `BaseNode`-classe (evita reescrever 800 linhas de
`nodes.py`); é uma tabela declarativa ao lado das funções.

`fixo=True`: o tipo faz parte do esqueleto do pipeline (classify + funis) —
não pode ser removido nem duplicado pela GUI. `fixo=False`: tipo de rota
terminal, pode ganhar novas instâncias (ex.: uma rota `FAQ` nova aponta pro
tipo `rag` com `config` diferente).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from src.application.orchestration import nodes
from src.application.orchestration.state import OraculoState

NodeFn = Callable[[OraculoState], Awaitable[dict]]


@dataclass(frozen=True)
class NodeType:
    nome: str
    fn: NodeFn
    display_name: str
    descricao: str
    categoria: str            # "sistema" | "rag" | "integracao" | "hitl" | "utilitario"
    icon: str                 # nome Lucide (mesmo set do Hub v2)
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    fixo: bool                # esqueleto do pipeline — GUI não mexe
    terminal: bool            # aresta natural é -> __end__

    def manifest(self) -> dict:
        return {
            "nome": self.nome, "display_name": self.display_name,
            "descricao": self.descricao, "categoria": self.categoria,
            "icon": self.icon,
            "io": {"reads": list(self.reads), "writes": list(self.writes)},
            "fixo": self.fixo, "terminal": self.terminal,
        }


_TIPOS: dict[str, NodeType] = {
    "classify": NodeType(
        "classify", nodes.classify_node,
        "Descobrir o assunto", "Passthrough do Supervisor: o `entrypoint` já "
        "classificou a rota; aqui só confirma o `state.route` (regex de reserva "
        "usada só no REPL).", "sistema", "git-branch",
        ("message", "route"), ("route",), fixo=True, terminal=False,
    ),
    "rag": NodeType(
        "rag", nodes.rag_node,
        "Responder com base nos documentos", "Recupera trechos no índice e "
        "sintetiza uma resposta ancorada, com cache semântico por rota.",
        "rag", "search",
        ("message", "rota", "history", "fatos"), ("answer",),
        fixo=False, terminal=True,
    ),
    "check_status": NodeType(
        "check_status", nodes.check_status_node,
        "Status de um pedido anterior", "Lê o histórico da última task Celery "
        "da sessão, sem acionar RAG.", "utilitario", "activity",
        ("session_id",), ("answer",), fixo=False, terminal=True,
    ),
    "greeting": NodeType(
        "greeting", nodes.greeting_node,
        "Saudação", "Resposta social rápida + registro do turno na memória.",
        "utilitario", "hand", ("message",), ("answer",), fixo=False, terminal=True,
    ),
    "media_download": NodeType(
        "media_download", nodes.media_download_node,
        "Baixar vídeo/mídia", "Dispara download (YouTube/Instagram) via chain "
        "Celery e responde 'download iniciado'; a entrega é assíncrona.",
        "integracao", "download", ("message", "user_context"), ("answer",),
        fixo=False, terminal=True,
    ),
    "sigaa": NodeType(
        "sigaa", nodes.sigaa_node,
        "Consultar dados no SIGAA", "Inicia o fluxo SIGAA (login CPF/senha via "
        "HITL fora do grafo, no `entrypoint`).", "integracao", "graduation-cap",
        ("message", "user_context"), ("answer", "status"), fixo=False, terminal=True,
    ),
    "human_handoff": NodeType(
        "human_handoff", nodes.human_handoff_node,
        "Encaminhar a um atendente humano", "Silencia o bot pra sessão (Redis "
        "`handoff:session:*`, TTL 24h), enfileira em `handoff:queue` e avisa o "
        "suporte.", "hitl", "life-buoy", ("session_id", "message", "history"),
        ("answer", "status", "handoff"), fixo=False, terminal=True,
    ),
    # ── Funil de ticket (esqueleto — 1 interrupt por nó) ────────────────────
    "ticket_ask_tipo": NodeType(
        "ticket_ask_tipo", nodes.ticket_ask_tipo,
        "Ticket: tipo", "Pergunta se é incidente ou requisição (RBAC no topo).",
        "hitl", "life-buoy", ("ticket_data", "ticket_error"),
        ("ticket_data", "ticket_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "ticket_ask_categoria": NodeType(
        "ticket_ask_categoria", nodes.ticket_ask_categoria,
        "Ticket: categoria", "Pergunta a categoria do problema.", "hitl",
        "life-buoy", ("ticket_data", "ticket_error"),
        ("ticket_data", "ticket_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "ticket_ask_queixa": NodeType(
        "ticket_ask_queixa", nodes.ticket_ask_queixa,
        "Ticket: descrição", "Pede a descrição livre do problema.", "hitl",
        "life-buoy", ("ticket_data", "ticket_error"),
        ("ticket_data", "ticket_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "ticket_confirm": NodeType(
        "ticket_confirm", nodes.ticket_confirm,
        "Ticket: confirmação", "Mostra o resumo e pede sim/não.", "hitl",
        "life-buoy", ("ticket_data", "ticket_error"),
        ("ticket_confirmed", "ticket_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "ticket_save": NodeType(
        "ticket_save", nodes.ticket_save,
        "Ticket: gravar", "Efeito colateral: grava o ticket (idempotente).",
        "hitl", "life-buoy", ("ticket_data",), ("answer",),
        fixo=True, terminal=True,
    ),
    # ── Funil de CRUD de cadastro ──────────────────────────────────────────
    "crud_ask_campo": NodeType(
        "crud_ask_campo", nodes.crud_ask_campo,
        "Cadastro: campo", "Pergunta se é setor ou telefone (RBAC no topo).",
        "hitl", "user-pen", ("crud_data", "crud_error"),
        ("crud_data", "crud_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "crud_ask_valor": NodeType(
        "crud_ask_valor", nodes.crud_ask_valor,
        "Cadastro: novo valor", "Pede o novo valor do campo escolhido.", "hitl",
        "user-pen", ("crud_data", "crud_error"),
        ("crud_data", "crud_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "crud_confirm": NodeType(
        "crud_confirm", nodes.crud_confirm,
        "Cadastro: confirmação", "Mostra o resumo e pede sim/não.", "hitl",
        "user-pen", ("crud_data", "crud_error"),
        ("crud_confirmed", "crud_error", "cancelado", "answer"),
        fixo=True, terminal=False,
    ),
    "crud_save": NodeType(
        "crud_save", nodes.crud_save,
        "Cadastro: gravar", "Efeito colateral: grava a atualização de cadastro.",
        "hitl", "user-pen", ("crud_data",), ("answer",),
        fixo=True, terminal=True,
    ),
}


def get_tipo(nome: str) -> NodeType:
    try:
        return _TIPOS[nome]
    except KeyError:
        raise ValueError(
            f"tipo de nó '{nome}' não existe no manifesto. "
            f"Disponíveis: {sorted(_TIPOS)}"
        )


def tipos_registrados() -> frozenset[str]:
    return frozenset(_TIPOS)


def manifest() -> list[dict]:
    """Catálogo pro Hub (`GET /hub/graph/nodes`)."""
    return [t.manifest() for t in _TIPOS.values()]
