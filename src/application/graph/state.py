# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 6: src/application/graph/state.py
# Responsabilidade: Contrato de estado imutável do LangGraph (Pydantic v2).
# ─────────────────────────────────────────────────────────────────────────────

"""
application/graph/state.py — OracleState v3 (Pydantic v2 + TypedDict híbrido)
==============================================================================
O estado do LangGraph deve ser um TypedDict (contrato da lib), mas validamos
com Pydantic v2 antes de injectar no grafo.

CAMPOS NOVOS NESTA VERSÃO:
  - _router_meta:       rastreabilidade do roteamento (método, latência, intent)
  - pending_tool_call:  dado pelo nó CRUD antes do HITL interrupt
  - tool_confirmation:  resposta do utilizador após interrupt ("sim"/"não")
  - awaiting_hitl:      flag que o Controller verifica para ignorar mensagens inúteis
  - lock_key:           chave do Redis Lock activo (para o Controller desbloquear)

NOTA SOBRE O LANGGRAPH INTERRUPT:
  O LangGraph interrompe o grafo com interrupt_before=["execute_tool_node"].
  O estado é persistido no checkpointer (Redis).
  A próxima mensagem do utilizador resume com invoke() passando o mesmo thread_id.
  O nó "confirm_node" lê tool_confirmation e decide: executar ou cancelar.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict

import operator


class OracleState(TypedDict, total=False):
    """
    Estado partilhado entre todos os nós do grafo LangGraph.

    Convenção de merge:
      - Listas com Annotated[list, operator.add]: acumulam (append semântico)
      - Outros campos: último writer vence (last-write-wins)

    Campos marcados com # [HITL] são críticos para o Human-in-the-Loop.
    """

    # ── Identificação e contexto do utilizador ────────────────────────────────
    session_id:        str              # número WhatsApp normalizado
    user_phone:        str              # +55XXXXXXXXXXX
    is_admin:          bool             # True se for o admin hardcoded no .env
    user_context:      dict             # {nome, matricula, periodo, curso, instituicao}

    # ── Input da conversa ────────────────────────────────────────────────────
    user_message:      str              # texto recebido do webhook
    historico:         str              # histórico compactado para o prompt

    # ── Roteamento ────────────────────────────────────────────────────────────
    route:             str              # nome do nó alvo (ex: "retrieve_node")
    crag_score:        float            # proxy de confiança do roteamento
    _router_meta:      Optional[dict]   # observabilidade: método, intent, latência

    # ── RAG / Retrieval ───────────────────────────────────────────────────────
    query_reescrita:   str              # HyDE ou query expandida
    contexto_rag:      Annotated[list[dict], operator.add]   # chunks recuperados
    crag_aprovado:     bool             # True se CRAG score ≥ threshold
    web_search_usado:  bool             # True se CRAG activou busca web

    # ── Geração de resposta ───────────────────────────────────────────────────
    resposta_final:    str              # resposta a ser enviada ao utilizador
    cache_hit:         bool             # True se veio do SemanticCache

    # ── CRUD / Tools ─────────────────────────────────────────────────────────
    tool_name:         str              # nome da tool a executar (ex: "update_email")
    tool_args:         dict             # argumentos da tool
    tool_result:       Optional[dict]   # resultado após execução [HITL]

    # ── Human-in-the-Loop [HITL] ──────────────────────────────────────────────
    pending_tool_call:  Optional[dict]  # {tool_name, tool_args, descricao_humana} [HITL]
    tool_confirmation:  Optional[str]   # "sim" | "não" | None [HITL]
    awaiting_hitl:      bool            # True = aguardando confirmação do user [HITL]
    hitl_message_sent:  bool            # True = mensagem de confirmação já enviada [HITL]

    # ── Lock / Concorrência ───────────────────────────────────────────────────
    lock_key:          Optional[str]    # chave do Redis Lock activo
    lock_token:        Optional[str]    # token do lock para release seguro

    # ── Observabilidade ───────────────────────────────────────────────────────
    trace_id:          str              # UUID da requisição para logs correlacionados
    node_timings:      Annotated[list[dict], operator.add]   # latência por nó
    error:             Optional[str]    # mensagem de erro para o utilizador