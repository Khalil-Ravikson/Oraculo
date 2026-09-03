"""
src/application/orchestration/entrypoint.py
===========================================
Entrypoint ÚNICO de processamento de mensagem (ADR 0008). Substitui os dois
dispatchers — `dispatcher.py` (legado) e `dispatcher_langgraph.py`.

Roda os fast-paths de front (STT, mídia sem legenda, labs REST/MCP),
guardrails de input, continuação de HITL legado, e então classifica via
`router/supervisor.py::rotear()` (o Supervisor real, 5 camadas — único ponto
de classificação do sistema). A rota vira ou uma invocação do `StateGraph`
(`builder.build_graph`) ou, enquanto `FEATURE_LANGGRAPH_NATIVE_ROUTES`
estiver desligada, uma delegação pra `dispatcher.py` nas 4 rotas condicionais
(GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS) — costura reversível da Fase 1
(ADR 0008); a Fase 3 remove a delegação e deleta `dispatcher.py`.

O circuit-breaker por agente (kill-switch de `/hub/agents`) roda aqui, uma
vez, para TODAS as rotas — antes vivia só em `dispatcher.py` e não valia
para as rotas nativas do grafo.

Guardrails de input (prompt injection/rate limit) e a continuação de HITL
legado (`handle_hitl_continuation`, SIGAA CPF/senha via
`hitl:session:{id}`) rodam DIRETO aqui (não mais só via delegação pro
dispatcher.py) — precisam existir neste módulo porque ele processa a
maioria das rotas sem nunca chamar dispatcher.py.

Classificação de rota reaproveita `router/supervisor.py::rotear()` (o
Supervisor real, 5 camadas) — não o classify_node interno do
langgraph_experiment (que é só um regex simplificado usado no teste via
CLI). Isso evita reabrir o problema dos "três cérebros" documentado em
`notas.md` item 5.1: aqui só tem UM classificador decidindo, o mesmo que
already roda em produção.

Classificação de rota reaproveita `router/supervisor.py::rotear()` (o
Supervisor real, 5 camadas) — não o `classify_node` do grafo (regex
simplificado, só usado no REPL `scripts/graph_repl.py`).

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
# Costura reversível da Fase 1 (ADR 0008): as 4 rotas condicionais
# (GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS) delegam pra cá enquanto
# `FEATURE_LANGGRAPH_NATIVE_ROUTES` estiver desligada. A Fase 3 remove este
# import e deleta `dispatcher.py`.
from src.application.runtime.dispatcher import processar as _processar_original

logger = logging.getLogger(__name__)

# A decisão "grafo vs dispatcher.py legado" por rota vem do `route_registry`
# (coluna `owner` + FEATURE_LANGGRAPH_NATIVE_ROUTES, migration 010):
#   owner="langgraph"              → tratada pelo grafo
#   owner="langgraph_conditional"  → grafo só com FEATURE_LANGGRAPH_NATIVE_ROUTES
#   owner="legacy"                 → sempre delegada pra dispatcher.py
#   permite_detour                 → pode interromper um funil HITL
#   entrypoint_node                → qual node do grafo (state.route)

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
        logger.info("🧪 [LANGGRAPH] Grafo compilado com AsyncRedisSaver (DB Redis dedicada) — checkpoint compartilhado entre API/workers.")
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
            logger.exception("⚠️  [LANGGRAPH] Falha ao fechar AsyncRedisSaver no shutdown.")
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
    (langgraph_experiment/nodes.py), todo funil novo nessa mesma sessão
    aceita a 1ª resposta e vai direto pro __end__, sem nunca perguntar o
    resto (reproduzido em teste real: 1x "sair", todo ticket seguinte
    quebrado).

    `rota`/`history`/`fatos` (Fase 3.5): contexto que antes se perdia ao
    entrar no grafo — só faz sentido pra `route == "rag"` (ticket/crud não
    usam RAG), mas incluir sempre é inofensivo (nodes.py só lê quando
    relevante).

    `user_context` (Fase 2d): idem, só usado pelos nodes nativos
    check_status/greeting/media_download/sigaa (ex.: chat_id de entrega),
    inofensivo pros demais."""
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
    # Fase 2d: nodes nativos portados de fast-paths que tinham seu próprio
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
    # Roda ANTES de tudo, inclusive dos labs REST/MCP abaixo — nota de voz
    # chega com `message` vazio, e ESTE módulo (não dispatcher.py) é o entry
    # point real chamado por process_message_task.py::_handle_message().
    # Bug real encontrado em produção nesta sessão: a interceptação de áudio
    # só existia em dispatcher.py::processar(), que só é chamado DAQUI quando
    # a rota classificada não é uma das que este módulo trata direto
    # (TICKET_ABERTURA/CRUD/RAG) — ou seja, nunca rodava pro caso mais comum
    # (voice note → rota GERAL, tratada direto aqui via LangGraph): `rotear("",
    # ...)` classificava a mensagem vazia como GERAL, e o node de RAG do
    # LangGraph ia pro embedding com query vazia (`EmbedContentRequest.content
    # contains an empty Part`, capturado/logado pelo SemanticCache mas sem
    # nunca transcrever o áudio de verdade). Ver notas.md seção 11.
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
        # Marca como consumido antes de qualquer delegação pra
        # dispatcher.py::processar() mais abaixo — evita baixar/transcrever o
        # MESMO áudio de novo lá (que mantém a mesma checagem como rede de
        # segurança pra outros consumidores diretos, ex.: admin hub/eval_api,
        # ou se o import em process_message_task.py voltar a apontar pro
        # dispatcher.py puro — ver nota "Ativado trocando o import" acima).
        user_context = {**user_context, "media_type": "", "msg_key_id": ""}
        logger.info("🎤 [LANGGRAPH] Áudio transcrito | session=%s | texto='%.60s'",
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

    # ── -0c. Guardrails de input + continuação de HITL legado (Fase 2d) ─────
    # Achado ao preparar a Fase 2d: este módulo nunca rodava
    # InputGuardrail/handle_hitl_continuation — dependia inteiramente de
    # delegar pra dispatcher.py::processar() (que roda os dois no topo) nas
    # rotas fora de _ROTAS_LANGGRAPH. Isso já deixava GERAL/CALENDARIO/
    # EDITAL/CONTATOS/WIKI/TICKET_ABERTURA/CRUD (sempre tratadas aqui, nunca
    # delegadas) sem guardrail de prompt injection/rate limit; migrar SIGAA
    # pra cá (Decisão 01) tornaria isso permanente pra 100% da produção, e
    # pioraria um bug de roteamento real: o HITL legado de SIGAA
    # (CPF/senha, `hitl:session:{id}`) não é visível pro Supervisor — sem
    # este check, uma sessão no meio da coleta de CPF podia ser
    # reclassificada pelo Supervisor pra uma rota RAG e o CPF digitado virar
    # query de busca em vez de continuar o login. Mesma ordem/posição
    # relativa que dispatcher.py já usa (guardrails antes, HITL legado
    # depois), só que aqui em vez de lá.
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
                    "🧪 [LANGGRAPH] Detour institucional (rota=%s) durante node=%s (session=%s)",
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
    from src.infrastructure.settings import settings

    rr = route_registry.get(decision.rota)

    # ── 1a. Circuit-breaker por agente (kill-switch de /hub/agents) ───────────
    # ADR 0008 (Fase 1): antes vivia só em `dispatcher.py::processar()` e não
    # valia pras rotas nativas do grafo (GERAL/CALENDARIO/EDITAL/CONTATOS/WIKI/
    # TICKET_ABERTURA/CRUD) — desligar um agente em `/hub/agents` não bloqueava
    # nada dessas. Aqui roda pra TODAS as rotas, antes de entrar no grafo ou
    # delegar. GREETING/MEDIA_DOWNLOAD/CHECK_STATUS têm `agente=NULL` (fast-
    # paths utilitários, sempre ligados). `dispatcher.py` mantém a mesma
    # checagem enquanto existir — a checagem dupla nas rotas delegadas é
    # inofensiva (mesmo resultado). Não roda no caminho de resume de funil
    # pendente (acima), mantendo a paridade com o comportamento anterior.
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

    if rr.delega_para_legado(settings.FEATURE_LANGGRAPH_NATIVE_ROUTES) \
            or rr.entrypoint_node not in route_registry.NODES_ENTRYPOINT:
        # Delega inteiro pro pipeline original. `decision_pronta=decision`:
        # bug real corrigido (log de produção) — sem isso `dispatcher.py`
        # reclassificava a MESMA mensagem do zero (2ª chamada Gemini Flash paga).
        return await _processar_original(message, session_id, user_context, history, fatos,
                                          decision_pronta=decision)

    route = rr.entrypoint_node
    logger.info("🧪 [LANGGRAPH] rota=%s → node=%s (session=%s)", decision.rota, route, session_id)

    payload = _reset_payload_para_rota(
        session_id, message, route,
        rota=decision.rota, history=history, fatos=fatos, user_context=user_context,
    )
    result = await app.ainvoke(payload, config=config)
    return _to_os_result(result, decision.rota, t0)
