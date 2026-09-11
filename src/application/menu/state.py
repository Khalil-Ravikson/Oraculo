"""
src/application/menu/state.py
=============================
Onde o usuário está no menu, por sessão. Chave Redis `menu:{session_id}`.

Mesmo molde de `capabilities/persistence/redis_state.py` (o HITL do SIGAA):
capability "burra", só encapsula Redis, nenhuma decisão de negócio. Quem
decide é `resolver.py`.

Por que Redis e não o checkpoint do LangGraph: a navegação de menu tem que
funcionar SEM invocar o grafo — é exatamente isso que faz navegar custar zero
token. Guardar a posição no state do grafo obrigaria a passar pelo grafo para
descobrir que não era preciso passar pelo grafo.

TTL de 30 minutos, alinhado com a memória de conversa (L1, `chat:{session_id}`)
— se o usuário sumiu por meia hora, ele volta pelo menu principal, que é o
comportamento que ele espera de um bot de WhatsApp.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field

MENU_STATE_PREFIX = "menu:"
MENU_STATE_TTL = 1800  # 30 min — mesmo horizonte da memória de conversa (L1)


class PerguntaPendente(BaseModel):
    """O usuário escolheu uma folha de "tirar dúvida" e o bot está esperando
    a pergunta. A próxima mensagem livre dele vira a query do RAG.

    É isso que permite o RAG rodar com a taxonomia certa sem classificador:
    `rota` e `doc_type` vêm da opção que ele apertou, não de um palpite de
    LLM sobre o texto."""

    rota: str
    doc_type: str
    filtros: dict[str, str] = Field(default_factory=dict)
    # Nó onde ele estava quando escolheu — para o 9 voltar ao lugar certo.
    origem: str = ""


class MenuEstado(BaseModel):
    """Posição atual + a pilha de como se chegou nela."""

    no: str = ""
    # Pilha de nós ancestrais. O 9 desempilha. Guardar a pilha em vez de um
    # ponteiro para o pai permite que o mesmo submenu seja alcançado de mais
    # de um lugar sem que o "voltar" fique ambíguo.
    pilha: list[str] = Field(default_factory=list)
    pendente: PerguntaPendente | None = None
    # Última rota respondida — a tela "Isso ajudou? / Fazer outra pergunta"
    # reaproveita a MESMA taxonomia, sem reclassificar nada (B6).
    ultima_rota: str = ""
    ultimo_doc_type: str = ""

    def empilhar(self, destino: str) -> "MenuEstado":
        return MenuEstado(
            no=destino,
            pilha=[*self.pilha, self.no] if self.no else [],
            ultima_rota=self.ultima_rota,
            ultimo_doc_type=self.ultimo_doc_type,
        )

    def desempilhar(self, raiz: str) -> "MenuEstado":
        if not self.pilha:
            return MenuEstado(
                no=raiz,
                ultima_rota=self.ultima_rota,
                ultimo_doc_type=self.ultimo_doc_type,
            )
        pilha = list(self.pilha)
        anterior = pilha.pop()
        return MenuEstado(
            no=anterior,
            pilha=pilha,
            ultima_rota=self.ultima_rota,
            ultimo_doc_type=self.ultimo_doc_type,
        )


async def get_menu_state(r: Any, session_id: str) -> MenuEstado | None:
    """None = sessão nova (ou expirada) — o chamador renderiza o menu raiz."""
    raw = await asyncio.to_thread(r.get, f"{MENU_STATE_PREFIX}{session_id}")
    if not raw:
        return None
    texto = raw if isinstance(raw, str) else raw.decode()
    return MenuEstado.model_validate_json(texto)


async def set_menu_state(
    r: Any, session_id: str, estado: MenuEstado, ttl: int = MENU_STATE_TTL
) -> None:
    payload = estado.model_dump_json()
    await asyncio.to_thread(r.setex, f"{MENU_STATE_PREFIX}{session_id}", ttl, payload)


async def delete_menu_state(r: Any, session_id: str) -> None:
    await asyncio.to_thread(r.delete, f"{MENU_STATE_PREFIX}{session_id}")
