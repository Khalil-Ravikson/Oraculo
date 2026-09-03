"""
src/application/orchestration/entrypoint.py
===========================================
Entrypoint ÚNICO de processamento de mensagem (ADR 0008). Sucessor dos dois
dispatchers, ambos deletados na Fase 3.

Roda: mute de atendimento humano, fast-paths de front (STT, mídia sem
legenda, labs REST/MCP), guardrails de input, continuação de HITL legado
(SIGAA CPF/senha), classificação via `router/supervisor.py::rotear()` (o
Supervisor real, 5 camadas — ÚNICO classificador do sistema), circuit-breaker
por agente (kill-switch de `/hub/agents`, para TODAS as rotas), e então
invoca o `StateGraph` (`builder.build_graph`). O `classify_node` do grafo é
passthrough em produção (regex, só usado no REPL `scripts/graph_repl.py`).

Checkpointer: `AsyncRedisSaver` (não `MemorySaver`) — obrigatório porque a
API e os workers Celery rodam em processos/containers diferentes; um
funil de ticket/CRUD que pausa (`interrupt()`) num processo e retoma noutro
precisa que o estado esteja em Redis, não em memória local.

ATENÇÃO — bug conhecido do pacote `langgraph-checkpoint-redis` na resumption
de múltiplos `interrupt()` pendentes no MESMO node (funciona no 1º resume,
quebra no 2º): https://github.com/langchain-ai/langgraph/issues/5074 e
https://github.com/redis-developer/langgraph-redis/issues/133. Reproduzido
nesta branch com o funil de ticket antigo (4 interrupts num node só).
Mitigado em `orchestration/nodes.py` quebrando cada funil em 1 node
por pergunta (1 interrupt por node) — ver docstring lá. Versão do pacote
pinada em `requirements.txt` por isso (área comprovadamente instável).

ATENÇÃO 2 — vazamento de estado entre execuções sucessivas do MESMO funil na
MESMA sessão: como o `thread_id` é fixo por sessão (`_thread_config`), o
LangGraph mantém o checkpoint indefinidamente entre invocações — inclusive
depois de chegar em END. Por isso, ao INICIAR um funil novo (não ao
retomar), o payload do `ainvoke()` precisa resetar explicitamente os campos
daquele funil (`_reset_payload_para_rota`), senão dados de uma execução
anterior (ex: um ticket já confirmado) vazam pra próxima. Ver notas.md.
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.application.runtime.contracts import OSResult

logger = logging.getLogger(__name__)

# Singleton de processo: correto desde que o processo Celery mantenha um único event loop
# vivo pela vida inteira (ver src/infrastructure/celery_app.py::run_in_worker_loop() +
# on_worker_process_init/on_worker_process_shutdown). O AsyncRedisSaver nasce e morre junto
# do processo, nunca atravessa uma fronteira de asyncio.run() — não há mais asyncio.run()
# nenhum nos entry points que chegam aqui (process_message_task.py).
_graph = None
_saver_cm = None
_setup_lock = asyncio.Lock()


async def _get_graph():
    global _graph, _saver_cm
    if _graph is not None:
        return _graph
    async with _setup_lock:
        if _graph is not None:
            return _graph

        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        from src.application.orchestration.builder import build_graph
        from src.infrastructure.settings import settings

        # Decisão 04 (Fase 2c) tentou isolar o checkpointer numa DB Redis
        # dedicada (/3), mesmo padrão de derivação que celery_app.py usa pro
        # broker (/1) e result backend (/2) — mas AsyncRedisSaver usa
        # RediSearch internamente pra indexar checkpoints (asetup() roda
        # FT.CREATE), e o módulo RediSearch só cria índice na DB/0
        # ("Cannot create index on db != 0", erro do servidor, confirmado em
        # produção). Diferente do Celery (broker/result backend não usam
        # RediSearch), o checkpointer é obrigado a ficar na mesma DB/0 do RAG
        # (idx:rag:chunks/idx:tools) — sem colisão funcional real porque os
        # prefixos de chave já são distintos (checkpoint:* vs rag:chunk:*/
        # tools:emb:*).
        _saver_cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
        saver = await _saver_cm.__aenter__()  # fechado explicitamente em on_worker_process_shutdown
        await saver.asetup()
        _graph = build_graph(saver)
        logger.info("🧭 [ORCH] Grafo compilado com AsyncRedisSaver — checkpoint compartilhado entre API/workers.")
    return _graph


async def aclose_graph() -> None:
    """Fecha o AsyncRedisSaver cacheado. Chamado por
    celery_app.py::on_worker_process_shutdown, rodando no mesmo loop persistente
    em que o saver foi aberto."""
    global _graph, _saver_cm
    if _saver_cm is not None:
        try:
            await _saver_cm.__aexit__(None, None, None)
        except Exception:
            logger.exception("⚠️  [ORCH] Falha ao fechar AsyncRedisSaver no shutdown.")
        finally:
            _saver_cm = None
            _graph = None


def _thread_config(session_id: str) -> dict:
    return {"configurable": {"thread_id": f"lg_ticket_{session_id}"}}


def _rota_from_route(route: str) -> str:
    from src.infrastructure import route_registry
    return route_registry.rota_do_node(route)


def _reset_payload_para_rota(
    session_id: str, message: str, route: str,
    rota: str = "", history: str = "", fatos: list[str] | None = None,
    user_context: dict | None = None,
) -> dict:
    """Payload inicial pro ainvoke() de um funil NOVO — reseta explicitamente
    os campos daquele funil (não os do outro), pra não herdar dado de uma
    execução anterior no mesmo thread_id (ver ATENÇÃO 2 na docstring do
    módulo). `cancelado` é sempre resetado independente da rota: sem isso,
    uma sessão que saiu de um funil anterior ("sair"/RBAC bloqueado) fica
    com cancelado=True gravado no checkpoint pra sempre — e como as edges
    condicionais checam state.cancelado ANTES de qualquer outra regra
    (orchestration/nodes.py), todo funil novo nessa mesma sessão
    aceita a 1ª resposta e vai direto pro __end__, sem nunca perguntar o
    resto (reproduzido em teste real: 1x "sair", todo ticket seguinte
    quebrado).

    `rota`/`history`/`fatos`: contexto que antes se perdia ao
    entrar no grafo — só faz sentido pra `route == "rag"` (ticket/crud não
    usam RAG), mas incluir sempre é inofensivo (nodes.py só lê quando
    relevante).

    `user_context`: usado pelos nós check_status/greeting/media_download/
    sigaa (ex.: chat_id de entrega), inofensivo pros demais."""
    payload = {
        "session_id": session_id, "message": message, "route": route, "cancelado": False,
        "rota": rota, "history": history, "fatos": fatos or [],
        "user_context": user_context or {},
    }
    if route == "ticket":
        payload.update(ticket_data={}, ticket_error="", ticket_confirmed=None)
    elif route == "crud":
        payload.update(crud_data={}, crud_error="", crud_confirmed=None)
    return payload


def _to_os_result(result: dict, rota: str, t0: float) -> OSResult:
    ms = int((time.monotonic() - t0) * 1000)
    interrupts = result.get("__interrupt__")
    if interrupts:
        pergunta = interrupts[0].value.get("question", "")
        return OSResult(
            answer=pergunta, plan_id="langgraph_hitl", rota=rota,
            cache_hit=False, total_ms=ms, status="hitl_pending",
        )
    # Nós portados de fast-paths que tinham seu próprio
    # HITL fora do interrupt()/checkpoint do LangGraph (ex.: sigaa_node,
    # HITL via hitl:session:* no Redis) podem devolver status="hitl_pending"
    # explícito no dict do node — sem __interrupt__ nenhum, porque o grafo
    # roda o node do início ao fim numa invocação só. rag/ticket/crud nunca
    # setam essa chave, então continuam caindo no default "ok" de sempre.
    return OSResult(
        answer=result.get("answer", ""), plan_id="langgraph_final", rota=rota,
        cache_hit=False, total_ms=ms, status=result.get("status", "ok"),
    )


async def processar(
    message: str,
    session_id: str,
    user_context: dict,
    history: str = "",
    fatos: list[str] | None = None,
) -> OSResult:
    t0 = time.monotonic()

    # ── -3. Sessão em atendimento humano (ADR 0008 Fase 2) ───────────────────
    # `human_handoff_node` silenciou o bot pra esta sessão. Não responde nada
    # (nem "digitando…") enquanto durar `handoff:session:{id}` — o atendente
    # humano assume. Sai do modo com `$voltar <jid>` (admin) ou o TTL de 24h.
    from src.infrastructure.redis_client import get_redis_text

    r = get_redis_text()
    try:
        if r.exists(f"handoff:session:{session_id}"):
            ms = int((time.monotonic() - t0) * 1000)
            return OSResult(
                answer="", plan_id="handoff_muted", rota="ESCALAR_HUMANO",
                cache_hit=False, total_ms=ms, status="handoff",
            )
    except Exception:  # noqa: BLE001 — Redis fora não pode travar o pipeline
        pass

    # ── -2. Fast-Path ÁUDIO (STT) ────────────────────────────────────────────
    # Nota de voz chega com `message` vazio — transcreve ANTES de classificar,
    # senão `rotear("")` classifica a mensagem vazia como GERAL e o RAG vai
    # pro embedding com query vazia (`EmbedContentRequest.content contains an
    # empty Part`). Ver notas.md seção 11.
    if user_context.get("media_type") == "audioMessage" and user_context.get("msg_key_id"):
        from src.application.runtime.audio_intake import _transcrever_audio_recebido
        from src.infrastructure.redis_client import get_redis_text

        r_stt = get_redis_text()
        transcript = await _transcrever_audio_recebido(r_stt, user_context, session_id)
        if transcript is None:
            ms = int((time.monotonic() - t0) * 1000)
            return OSResult(
                answer="😕 Não consegui entender o áudio. Pode tentar de novo ou escrever a mensagem?",
                plan_id="stt_error", rota="AUDIO_TRANSCRIBE", cache_hit=False,
                total_ms=ms, status="error",
            )
        message = f"{message.strip()}\n{transcript}" if message.strip() else transcript
        # Marca como consumido pra não retranscrever.
        user_context = {**user_context, "media_type": "", "msg_key_id": ""}
        logger.info("🎤 [ORCH] Áudio transcrito | session=%s | texto='%.60s'",
                    session_id, message)

    # ── -1c. Fast-Path MÍDIA SEM LEGENDA (imagem/sticker/vídeo/documento) ────
    # Vision ainda não existe (Fase 4/5, não implementado) — sem essa
    # checagem, mídia sem legenda seguia com `message` vazio até o RAG do
    # LangGraph, gerando `EmbedContentRequest.content contains an empty Part`
    # (mesma classe de bug do áudio acima, sem tratamento). O bloco de áudio
    # já garante `message` não-vazio quando a transcrição dá certo (e retorna
    # cedo quando falha) — chegar aqui com `message` vazio só é possível pra
    # outros tipos de mídia. Ver notas.md seção 11.
    if not message.strip() and user_context.get("has_media"):
        ms = int((time.monotonic() - t0) * 1000)
        return OSResult(
            answer="📎 Recebi seu arquivo, mas ainda não consigo analisar imagens/vídeos/documentos. "
                   "Me conta em texto o que você precisa que eu tento ajudar!",
            plan_id="unsupported_media", rota="UNSUPPORTED_MEDIA", cache_hit=False,
            total_ms=ms, status="ok",
        )

    # ── -1. Laboratório REST (`rest_lab/`, branch/worktree `research/rest-mcp-estudos`) ──
    # Intercepta ANTES de tudo (mesmo antes de checar funil HITL pendente):
    # comandos "rest ..." são estudo isolado, sem estado/interrupt, não fazem
    # parte de nenhum funil real. `tentar_rotear` só intercepta mensagens que
    # começam literalmente com "rest " — qualquer outra coisa devolve `None`
    # e cai no fluxo normal abaixo, sem custo nem risco de colidir com uma
    # pergunta acadêmica real do aluno. Ver rest_lab/router.py.
    from rest_lab.router import tentar_rotear

    rest_resultado = await tentar_rotear(message)
    if rest_resultado is not None:
        ms = int((time.monotonic() - t0) * 1000)
        return OSResult(
            answer=rest_resultado["mensagem"], plan_id="rest_lab", rota="REST_LAB",
            cache_hit=False, total_ms=ms, status="ok",
        )

    # ── -1b. Laboratório MCP (`mcp_lab/`, mesma branch/worktree) ──
    # Mesmo desenho do laboratório REST acima: intercepta ANTES de tudo,
    # prefixo próprio ("stack ...") não colide com "rest ..." nem com
    # pergunta acadêmica real. Ver mcp_lab/router.py e mcp_lab/__init__.py.
    from mcp_lab.router import tentar_rotear as mcp_tentar_rotear

    mcp_resultado = await mcp_tentar_rotear(
        message, chat_id=user_context.get("chat_id") or session_id
    )
    if mcp_resultado is not None:
        ms = int((time.monotonic() - t0) * 1000)
        return OSResult(
            answer=mcp_resultado["mensagem"], plan_id="mcp_lab", rota="MCP_LAB",
            cache_hit=False, total_ms=ms, status="ok",
        )

    # ── -0c. Guardrails de input + continuação de HITL legado ────────────────
    # Guardrails ANTES do HITL: se a sessão está no meio da coleta de CPF/senha
    # do SIGAA (`hitl:session:{id}`, invisível pro Supervisor), sem este check
    # o Supervisor reclassificaria a mensagem e o CPF digitado viraria query de
    # busca em vez de continuar o login.
    from src.application.chain.guardrails import get_input_guardrail

    def _validate_sync():
        return get_input_guardrail().validate(message, session_id, r)

    guard_ok, text_or_error = await asyncio.to_thread(_validate_sync)
    if not guard_ok:
        ms = int((time.monotonic() - t0) * 1000)
        return OSResult(
            answer=text_or_error, plan_id="", rota="BLOCKED",
            cache_hit=False, total_ms=ms, status="error",
        )
    message = text_or_error  # sanitizado

    from src.agents.sigaa.auth_flow import handle_hitl_continuation

    hitl_result = await handle_hitl_continuation(message, session_id, user_context, r)
    if hitl_result is not None:
        return hitl_result

    app = await _get_graph()
    config = _thread_config(session_id)

    state = await app.aget_state(config)

    # ── 0. Retomada de um interrupt() pendente (funil de ticket/CRUD em andamento) ──
    if state.next:
        from src.application.orchestration.nodes import VALIDATORS_POR_NODE, _eh_saida, responder_rag_direto

        node_pendente = state.next[0]
        # Comando de saída ("sair"/"cancelar"/...) tem prioridade sobre o
        # validador do node E sobre o detour de RAG abaixo — sem isso, "sair"
        # não validava pro passo pendente, virava detour, o RAG respondia
        # "não encontrei" e a mesma pergunta pendente era repetida, sem
        # nunca sair de fato do funil (bug observado em teste real).
        if _eh_saida(message):
            parece_valida = True
        else:
            validador = VALIDATORS_POR_NODE.get(node_pendente)
            parece_valida = validador(state.values, message) if validador else True

        if not parece_valida:
            # Pode ser um "detour": pergunta institucional solta no meio do
            # funil, em vez de resposta pro passo pendente — reclassifica
            # antes de rejeitar (ver .claude.md/notas.md, decisão: detour
            # limitado a rotas RAG diretas, não expande pra SIGAA/outras).
            from src.router.supervisor import rotear

            decision = await rotear(message, session_id, user_context)
            from src.infrastructure import route_registry
            if route_registry.get(decision.rota).permite_detour:
                pergunta_pendente = ""
                if state.tasks and state.tasks[0].interrupts:
                    pergunta_pendente = state.tasks[0].interrupts[0].value.get("question", "")
                # history/fatos não disponíveis aqui (resposta a um interrupt
                # pendente, não a invocação inicial) — sem regressão: hoje já
                # não tinha nenhum contexto nesse ponto.
                resposta_rag = await responder_rag_direto(
                    message, rota=decision.rota, session_id=session_id,
                )
                answer = f"{resposta_rag}\n\n📋 Voltando ao que estávamos fazendo:\n{pergunta_pendente}"
                ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "🧭 [ORCH] Detour institucional (rota=%s) durante node=%s (session=%s)",
                    decision.rota, node_pendente, session_id,
                )
                return OSResult(
                    answer=answer, plan_id="langgraph_hitl_detour", rota=_rota_from_route(state.values.get("route", "")),
                    cache_hit=False, total_ms=ms, status="hitl_pending",
                )
            # Não pareceu detour nem resposta válida → deixa o próprio node
            # rejeitar e re-perguntar (comportamento padrão de validação).

        from langgraph.types import Command

        rota = _rota_from_route(state.values.get("route", ""))
        result = await app.ainvoke(Command(resume=message), config=config)
        return _to_os_result(result, rota, t0)

    # ── 1. Classificação (reaproveita o Supervisor real, não duplica regra) ────
    from src.router.supervisor import rotear

    decision = await rotear(message, session_id, user_context)

    from src.infrastructure import route_registry

    rr = route_registry.get(decision.rota)

    # ── 1a. Circuit-breaker por agente (kill-switch de /hub/agents) ───────────
    # ADR 0008: roda pra TODAS as rotas antes de entrar no grafo (antes vivia
    # só no `dispatcher.py` legado). GREETING/MEDIA_DOWNLOAD/CHECK_STATUS/
    # ESCALAR_HUMANO têm `agente=NULL` (utilitários, sempre ligados). Não roda
    # no caminho de resume de funil pendente — só numa mensagem nova.
    if rr.agente:
        from src.capabilities.persistence.agent_config import is_agent_enabled
        if not await is_agent_enabled(r, rr.agente):
            ms = int((time.monotonic() - t0) * 1000)
            logger.info("🚧 [ORCH] Agente '%s' desativado — rota %s bloqueada (session=%s)",
                        rr.agente, decision.rota, session_id)
            return OSResult(
                answer="🚧 Essa função está temporariamente desativada. Tente novamente mais tarde.",
                plan_id="agent_disabled", rota=decision.rota,
                cache_hit=False, total_ms=ms, status="ok",
            )

    # Rota classificada mas cujo `entrypoint_node` não é um nó real do grafo
    # (ex.: rota personalizada mal configurada no /hub/routes) → trata como
    # `rag` (o `_UNKNOWN` já aponta pra lá; isto é só a rede de segurança).
    route = rr.entrypoint_node
    if route not in route_registry.NODES_ENTRYPOINT:
        logger.warning("⚠️  [ORCH] rota=%s tem entrypoint_node inválido '%s' — usando 'rag'",
                       decision.rota, route)
        route = "rag"

    logger.info("🧭 [ORCH] rota=%s → node=%s (session=%s)", decision.rota, route, session_id)

    payload = _reset_payload_para_rota(
        session_id, message, route,
        rota=decision.rota, history=history, fatos=fatos, user_context=user_context,
    )
    result = await app.ainvoke(payload, config=config)
    return _to_os_result(result, decision.rota, t0)
