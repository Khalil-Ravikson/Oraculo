from __future__ import annotations

from pydantic import BaseModel, Field


class OraculoState(BaseModel):
    session_id: str = ""
    message: str = ""
    route: str = ""          # "rag" | "ticket" | "crud" — decide as edges do grafo
    answer: str = ""

    # ── Contexto compartilhado (Fase 3.5 — cache/memória no LangGraph) ────────
    # `rota` é a classificação FINA do Supervisor (CALENDARIO/EDITAL/CONTATOS/
    # WIKI/GERAL/...), distinta de `route` acima — antes desses campos existirem
    # ela se perdia ao entrar no grafo (colapsada em "rag" só pra decidir a
    # edge), e RAGSearchService/SynthesisService sempre rodavam com defaults
    # genéricos (doc_type="geral", rota="GERAL"), mesmo pra pergunta de EDITAL.
    # `history`/`fatos` (Camadas L1/L4 da memória cognitiva) tinham o mesmo
    # problema — chegavam em `processar()` mas nunca entravam no state.
    rota: str = ""
    history: str = ""
    fatos: list[str] = Field(default_factory=list)
    # Dados do WhatsApp que alguns nós precisam além de session_id/message —
    # hoje só `chat_id` (MEDIA_DOWNLOAD/SIGAA, destino de entrega via Celery,
    # ver `enviar_resposta_whatsapp_task`). Dict simples pelo mesmo motivo de
    # ticket_data/crud_data abaixo (serialização msgpack do checkpointer).
    user_context: dict = Field(default_factory=dict)

    # ── Controle de fluxo dos nós de front (ADR 0008) ────────────────────────
    # `status` — "ok" | "error" | "hitl_pending" | "handoff". `_to_os_result`
    # lê daqui (antes só `sigaa_node` setava). `early_exit` — um nó de front
    # (intake/gate/policy) resolveu a mensagem e o grafo deve ir direto pro
    # __end__ sem passar pelo fan-out. `blocked` — bloqueio de guardrail de
    # input. `handoff` — sessão foi encaminhada a um atendente humano.
    status: str = "ok"
    early_exit: bool = False
    blocked: bool = False
    handoff: bool = False
    # `plan_id` da resposta final (`_to_os_result` lê daqui, default
    # "langgraph_final" se vazio) — nós de front que resolvem a mensagem sem
    # passar pelo fan-out (ex.: classify_node com circuit-breaker acionado)
    # marcam um id específico ("agent_disabled") pro dashboard/eval saberem
    # POR QUE a resposta saiu curta, sem reprocessar heurística nenhuma.
    plan_id: str = ""

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
