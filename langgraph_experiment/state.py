from __future__ import annotations

from pydantic import BaseModel, Field


class OraculoState(BaseModel):
    session_id: str = ""
    message: str = ""
    route: str = ""          # "rag" | "ticket" | "crud"
    answer: str = ""

    # ── Funil de ticket ──────────────────────────────────────────────────────
    # dict simples (não um BaseModel aninhado): LangGraph serializa o estado
    # via msgpack pro checkpointer, e tipos Pydantic customizados aninhados
    # exigem registro explícito (allowed_msgpack_modules) — dict é nativo,
    # sem esse risco. Chaves esperadas: tipo, categoria, queixa (todas str|None).
    ticket_data: dict = Field(default_factory=dict)
    # Mensagem de erro a prefixar na próxima pergunta do funil de ticket quando
    # a resposta do usuário não valida — consumida pela edge condicional do
    # node correspondente pra decidir re-perguntar em vez de aceitar qualquer
    # texto silenciosamente (ver notas.md/plano da investigação HITL).
    ticket_error: str = ""
    ticket_confirmed: bool | None = None

    # ── Funil de CRUD de cadastro (mesmo padrão do ticket) ──────────────────
    crud_data: dict = Field(default_factory=dict)  # chaves: campo, valor
    crud_error: str = ""
    crud_confirmed: bool | None = None

    # Sinaliza saída explícita do HITL (comando "sair"/"cancelar" em qualquer
    # pergunta do funil) OU bloqueio de RBAC — as edges condicionais checam
    # isso ANTES de qualquer outra regra e vão direto pro __end__, sem cair
    # no detour de RAG nem re-perguntar. Ver nodes.py::_eh_saida().
    cancelado: bool = False
