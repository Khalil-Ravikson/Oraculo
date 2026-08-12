"""
src/application/runtime/dispatcher.py
========================================
Runtime orchestration entry point — ex `application/chain/cognitive_os.py`
(Fase 3 do PLANO_REFATORACAO_SUPERVISOR.md).

Orquestra o pipeline orientado a eventos:
  1. HITL (agents/sigaa/auth_flow.py) → continuação de fluxo pendente, se houver
  2. Orchestrator + Supervisor (router/) → classifica e retorna do cache se possível
  3. Fast-paths (GREETING, MEDIA_DOWNLOAD, SIGAA) → resposta imediata ou HITL
  4. Planner → gera DAG de execução
  5. Despacha workers via Celery (desacoplado)

Para requests síncronos (admin hub/eval, via `_aguardar_resposta_final`):
  Modo "fast": aguarda a resposta com timeout de 15s.

MÉTRICAS:
  oraculo_cognitive_os_latency_ms (histogram)
  oraculo_cognitive_os_requests_total{status}

NOTA DE ESCOPO: este módulo é puramente "cola" mecânica (monta a chain Celery
a partir de uma decisão já tomada) + o entry point de orquestração em si.
Ele não decide regra de negócio SIGAA (isso é `agents/sigaa/auth_flow.py`) nem
faz IO Redis cru (isso é `capabilities/persistence/redis_state.py`).
`application/chain/cognitive_os.py` permanece como shim de compatibilidade
(não foi deletado como o plano original previa) porque `_despachar_workers`,
`_aguardar_resposta_final` e `processar` têm consumidores externos vivos além
de `process_message_task.py`: `api/chain_sse.py`, `api/routers/web/hub.py` e
`api/routers/admin/eval_api.py` chamam esses símbolos diretamente para
implementar um modo síncrono de debug (aguardam a resposta final via polling
em vez do fluxo assíncrono via `enviar_resposta_whatsapp_task`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from prometheus_client import Counter, Histogram

from src.capabilities.persistence import redis_state
from src.capabilities.persistence.redis_state import (
    RESULTS_CACHE_PREFIX,
    RESULTS_TTL,
    STREAM_FINAL_RESPONSES,
)

logger = logging.getLogger(__name__)

# ── Métricas ──────────────────────────────────────────────────────────────────
_OS_LATENCY = Histogram(
    "oraculo_cognitive_os_latency_ms",
    "Latência total do CognitiveOS em ms",
    buckets=[100, 250, 500, 1000, 2000, 5000, 10000],
)
_OS_REQUESTS = Counter(
    "oraculo_cognitive_os_requests_total",
    "Total de requisições pelo CognitiveOS",
    ["status"],
)

# Timeout máximo esperando resposta do pipeline (usado pelo modo síncrono do admin hub)
RESPONSE_TIMEOUT_S = 15.0
POLL_INTERVAL_S    = 0.2

# Circuit-breaker por agente (liga/desliga em /hub/agents, ver agent_config.py).
# GREETING e MEDIA_DOWNLOAD não são "agentes" (fast-paths utilitários) — ficam
# sempre ligados. Rotas fora deste mapa também não são gateadas.
_ROTA_PARA_AGENTE = {
    "GERAL": "academic_knowledge", "CALENDARIO": "academic_knowledge",
    "EDITAL": "academic_knowledge", "CONTATOS": "academic_knowledge",
    "WIKI": "academic_knowledge",
    "SIGAA": "sigaa",
    "CRUD": "tickets",
    "TICKET_ABERTURA": "tickets",
}


@dataclass
class OSResult:
    answer: str
    plan_id: str
    rota: str
    cache_hit: bool
    total_ms: int
    status: str   # "ok" | "timeout" | "error" | "hitl_pending"
    error: str = ""
    action_buttons: list = field(default_factory=list)


async def processar(
    message: str,
    session_id: str,
    user_context: dict,
    history: str = "",
    fatos: list[str] | None = None,
) -> OSResult:
    """
    Entry point do runtime. Substitui `cognitive_os.processar()`.
    """
    t0 = time.monotonic()
    fatos = fatos or []

    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

    # ── -1. Fast-Path ÁUDIO (STT) ────────────────────────────────────────────
    # Nota de voz chega com `message` vazio (só a legenda, se houver) — roda
    # ANTES de guardrails/HITL/orchestrator pra que tudo downstream processe
    # o texto transcrito como se o usuário tivesse digitado. Ver roadmap:
    # notas.md seção 11 / arquitetura_oraculo.md seção 10.
    if user_context.get("media_type") == "audioMessage" and user_context.get("msg_key_id"):
        transcript = await _transcrever_audio_recebido(r, user_context, session_id)
        if transcript is None:
            ms = int((time.monotonic() - t0) * 1000)
            _OS_REQUESTS.labels(status="stt_error").inc()
            return OSResult(
                answer="😕 Não consegui entender o áudio. Pode tentar de novo ou escrever a mensagem?",
                plan_id="stt_error", rota="AUDIO_TRANSCRIBE", cache_hit=False,
                total_ms=ms, status="error",
            )
        message = f"{message.strip()}\n{transcript}" if message.strip() else transcript
        logger.info("🎤 [DISPATCHER] Áudio transcrito | session=%s | texto='%.60s'",
                    session_id, message)

    # ── -1b. Fast-Path MÍDIA SEM LEGENDA (imagem/sticker/vídeo/documento) ────
    # Vision ainda não existe (roadmap Fase 4/5, não implementado) — sem essa
    # checagem, mídia sem legenda seguia com `message` vazio até o RAG,
    # gerando `EmbedContentRequest.content contains an empty Part` (mesma
    # classe de bug do áudio acima, só que sem tratamento nenhum). O bloco de
    # áudio acima já garante `message` não-vazio quando a transcrição dá
    # certo (e retorna cedo quando falha) — chegar aqui com `message` vazio
    # só é possível pra outros tipos de mídia. Ver notas.md seção 11.
    if not message.strip() and user_context.get("has_media"):
        ms = int((time.monotonic() - t0) * 1000)
        return OSResult(
            answer="📎 Recebi seu arquivo, mas ainda não consigo analisar imagens/vídeos/documentos. "
                   "Me conta em texto o que você precisa que eu tento ajudar!",
            plan_id="unsupported_media", rota="UNSUPPORTED_MEDIA", cache_hit=False,
            total_ms=ms, status="ok",
        )

    # ── Guardrails (Entrada) ──────────────────────────────────────────────────
    from src.application.chain.guardrails import get_input_guardrail

    def _validate_sync():
        return get_input_guardrail().validate(message, session_id, r)

    ok, text_or_error = await asyncio.to_thread(_validate_sync)
    if not ok:
        return OSResult(answer=text_or_error, plan_id="", rota="BLOCKED",
                        cache_hit=False, total_ms=0, status="error")
    message = text_or_error  # sanitizado

    try:
        # ── 0. HITL Interception ──────────────────────────────────────────────
        from src.agents.sigaa.auth_flow import handle_hitl_continuation
        hitl_result = await handle_hitl_continuation(message, session_id, user_context, r)
        if hitl_result is not None:
            return hitl_result

        # ── 0a. Continuação de rascunho de ticket/CRUD (agents/tickets/) ──────
        # Chaves próprias (ticket_draft:*, crud_update_draft:*), checadas antes
        # do roteamento normal — mesmo padrão do HITL do SIGAA acima, mas sem
        # colidir com hitl:session:*.
        from src.agents.tickets.ticket_flow import handle_ticket_continuation
        ticket_result = await handle_ticket_continuation(message, session_id, user_context, r)
        if ticket_result is not None:
            return ticket_result

        from src.agents.tickets.crud_tool import handle_crud_continuation
        crud_result = await handle_crud_continuation(message, session_id, user_context, r)
        if crud_result is not None:
            return crud_result

        # ── 0b. Fast Path: comandos explícitos ───────────────────────────────
        # ! @ $ → vai direto pro router semântico existente (sem gastar tokens no LLM)
        # linguagem natural → Orchestrator decide a ação
        is_command = message.startswith(("!", "@", "$"))

        if not is_command:
            from src.router.llm_fallback import orchestrate
            from src.memory.services.redis_memory_service import get_cognitive_memory

            mem = get_cognitive_memory()
            op_mem = await mem.get_operational(session_id)

            orch_decision = await orchestrate(
                message=message,
                history_summary=await mem.format_history(session_id),
                task_history=await mem.get_task_history(session_id),
                operational_memory=op_mem,
                user_context=user_context,
                session_id=session_id,
            )

            logger.info(f"⏱️ Tempo Orquestrador: {time.monotonic() - t0}s")

            # Atualiza operational memory
            await mem.set_operational(session_id, {
                "last_action": orch_decision.action,
                "route_hint": orch_decision.route_hint,
                "status": "routing",
            })

            # check_status → responde com o histórico de task sem acionar RAG
            if orch_decision.action == "check_status":
                th = await mem.get_task_history(session_id)
                answer = (
                    f"Última tarefa: *{th.get('last_worker', '?')}*\n"
                    f"Resultado: {th.get('last_result', 'Nenhuma tarefa anterior encontrada.')}"
                ) if th else "Nenhuma tarefa anterior registrada nesta sessão."
                ms = int((time.monotonic() - t0) * 1000)
                return OSResult(answer=answer, plan_id="check_status",
                                rota="GERAL", cache_hit=True, total_ms=ms, status="ok")

            # reply_direct → greeting inline
            if orch_decision.action == "reply_direct":
                decision_rota = "GREETING"
            # call_sigaa → força rota SIGAA
            elif orch_decision.action == "call_sigaa":
                decision_rota = "SIGAA"
            elif orch_decision.action == "call_media":
                decision_rota = "MEDIA_DOWNLOAD"
            elif orch_decision.action == "call_ticket":
                decision_rota = "TICKET_ABERTURA"
            elif orch_decision.action == "call_crud_update":
                decision_rota = "CRUD"
            else:
                # call_rag → usa route_hint do orquestrador
                decision_rota = orch_decision.route_hint or "GERAL"
        else:
            decision_rota = None  # deixa o Supervisor decidir

        # ── 1. Supervisor (só para comandos ou quando o Orchestrator pediu RAG) ──
        from src.router.supervisor import rotear
        decision = await rotear(message, session_id, user_context)

        # Orchestrator tem prioridade sobre o Supervisor para linguagem natural
        if not is_command and decision_rota:
            # BUG corrigido: antes só `decision.rota` era trocado, e o
            # `dag_hint` ficava com o valor calculado para a rota ORIGINAL do
            # Supervisor (ex: rota virava "GERAL" mas o hint ainda dizia
            # {"steps": ["ticket_abertura"]}). O Planner (Gemini Pro) recebia
            # rota e hint contraditórios e "resolvia" sozinho escolhendo um
            # worker da sua whitelist que nem existe de verdade — daí o erro
            # "Falha ao localizar worker crud_confirm no registry". Rota e
            # hint têm que mudar juntos.
            from src.router.supervisor import _dag_hint_para_rota
            decision.rota = decision_rota
            decision.dag_hint = _dag_hint_para_rota(decision_rota, message)

        # ── Circuit-breaker por agente (liga/desliga em /hub/agents) ──────────
        from src.capabilities.persistence.agent_config import is_agent_enabled
        agente_da_rota = _ROTA_PARA_AGENTE.get(decision.rota)
        if agente_da_rota and not await is_agent_enabled(r, agente_da_rota):
            ms = int((time.monotonic() - t0) * 1000)
            _OS_LATENCY.observe(ms)
            _OS_REQUESTS.labels(status="agent_disabled").inc()
            return OSResult(
                answer="🚧 Essa função está temporariamente desativada. Tente novamente mais tarde.",
                plan_id="agent_disabled",
                rota=decision.rota,
                cache_hit=False,
                total_ms=ms,
                status="ok",
            )

        # Cache HIT da Rota: roteador identificou uma intenção rápida ou já cacheadas
        if decision.cache_hit:
            _OS_REQUESTS.labels(status="cache_hit").inc()
            cached_answer = _buscar_resposta_cached(decision)
            if cached_answer:
                ms = int((time.monotonic() - t0) * 1000)
                _OS_LATENCY.observe(ms)
                return OSResult(
                    answer=cached_answer,
                    plan_id="cache",
                    rota=decision.rota,
                    cache_hit=True,
                    total_ms=ms,
                    status="ok",
                )

        # 1b. Semantic Cache de Respostas (Cosine Similarity > 0.92)
        if decision_rota or decision.rota:
            rota_efetiva = decision_rota or decision.rota
            if rota_efetiva not in ("SIGAA", "MEDIA_DOWNLOAD", "GREETING"):
                from src.infrastructure.semantic_cache import SemanticCache
                sem_cache = SemanticCache(threshold=0.92)

                cached_response = await sem_cache.get(query=message, rota=rota_efetiva)
                if cached_response:
                    _OS_REQUESTS.labels(status="cache_hit").inc()
                    ms = int((time.monotonic() - t0) * 1000)
                    _OS_LATENCY.observe(ms)
                    return OSResult(
                        answer=cached_response.get("answer", ""),
                        plan_id="sem_cache",
                        rota=rota_efetiva,
                        cache_hit=True,
                        total_ms=ms,
                        status="ok",
                        action_buttons=cached_response.get("action_buttons", [])
                    )

        # ── Fast-Path GREETING ────────────────────────────────────────────────
        if decision.rota == "GREETING":
            import random
            saudacoes = [
                "Olá! 😊 Sou o Oráculo UEMA. Como posso ajudar?",
                "Oi! Em que posso ajudá-lo(a) hoje?",
                "Olá! Pode perguntar sobre calendário, editais, contatos ou suporte. 🎓",
            ]
            resposta = random.choice(saudacoes) + (
                "\n\n🔧 *Ferramentas do usuário* (demonstração):\n"
                "• !ytb — baixar vídeo do YouTube\n"
                "• !sticker — criar figurinha"
            )

            from src.memory.services.redis_memory_service import get_cognitive_memory
            mem = get_cognitive_memory()
            await mem.add_turn(session_id, "user", message)
            await mem.add_turn(session_id, "assistant", resposta)

            ms = int((time.monotonic() - t0) * 1000)
            _OS_LATENCY.observe(ms)
            _OS_REQUESTS.labels(status="ok").inc()
            return OSResult(
                answer=resposta,
                plan_id="fast_greeting",
                rota=decision.rota,
                cache_hit=False,
                total_ms=ms,
                status="ok"
            )

        # ── Fast-Path MEDIA_DOWNLOAD ──────────────────────────────────────────
        if decision.rota == "MEDIA_DOWNLOAD":
            import re
            urls = re.findall(r'(https?://\S+)', message)
            if urls:
                url = urls[0]
            else:
                # Sem URL na mensagem — pode ser busca por termo ("buscar
                # vídeo sobre X", ver router/supervisor.py::_RE_YTB_BUSCA).
                # Sem essa checagem, a mensagem INTEIRA vira "url" e o yt-dlp
                # falha com "not a valid URL" (bug real encontrado nesta
                # sessão — este Fast-Path é um caminho paralelo ao Planner/
                # `_dag_hint_para_rota`, não reaproveita aquela lógica).
                from src.router.supervisor import _RE_YTB_BUSCA
                match_busca = _RE_YTB_BUSCA.search(message)
                url = f"ytsearch1:{match_busca.group(1).strip()}" if match_busca else message

            from src.application.workers.registry import _autodiscover_workers, _REGISTRY
            from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
            from celery import chain

            _autodiscover_workers()
            worker_name = "insta_download" if "instagram" in url.lower() else "ytb_download"
            fn = _REGISTRY.get(worker_name)

            plan_id = f"fast_media_{int(time.time())}"
            if fn:
                event = {
                    "plan_id": plan_id,
                    "session_id": session_id,
                    # `chat_id` (JID de grupo `@g.us` ou contato `@s.whatsapp.net`)
                    # é o destino de envio de verdade — `session_id`/`phone` é
                    # só a chave de sessão/memória, a Evolution API rejeita
                    # mídia mandada pro número "cru" (bug real encontrado
                    # nesta sessão: 400 Bad Request, jid "não existe").
                    "chat_id": user_context.get("chat_id") or session_id,
                    "step_id": "s1",
                    "url": url,
                    "query": message,
                    "hitl_confirmed": True
                }
                delivery_ctx = {
                    "plan_id": plan_id,
                    "chat_id": user_context.get("chat_id") or session_id,
                    "sender_jid": session_id,
                    "route": "MEDIA_DOWNLOAD",
                    "query": message,
                }
                workflow = chain(
                    fn.s(event),
                    enviar_resposta_whatsapp_task.s(delivery_ctx)
                )
                workflow.apply_async()
            else:
                logger.error("❌ worker '%s' não encontrado no Registry.", worker_name)

            ms = int((time.monotonic() - t0) * 1000)
            _OS_LATENCY.observe(ms)
            _OS_REQUESTS.labels(status="ok").inc()

            return OSResult(
                answer="📥 **Download iniciado!**\nO arquivo será enviado aqui em instantes. Aguarde...",
                plan_id=plan_id,
                rota=decision.rota,
                cache_hit=True,
                total_ms=ms,
                status="ok"
            )

        # ── Fast-Path SIGAA (HITL de autenticação ou sessão já ativa) ─────────
        if decision.rota == "SIGAA":
            from src.agents.sigaa.auth_flow import start_or_continue_sigaa
            sigaa_result = await start_or_continue_sigaa(decision, message, session_id, user_context, r, t0)
            if sigaa_result is not None:
                _OS_LATENCY.observe(sigaa_result.total_ms)
                _OS_REQUESTS.labels(status="ok").inc()
                return sigaa_result
            # None → hitl_confirmed=True, cai pro Planner normal abaixo

        # ── Fast-Path TICKET_ABERTURA (funil de chamado, agents/tickets/) ─────
        if decision.rota == "TICKET_ABERTURA":
            from src.agents.tickets.ticket_flow import start_ticket_abertura
            ticket_start_result = await start_ticket_abertura(decision, message, session_id, user_context, r, t0)
            _OS_LATENCY.observe(ticket_start_result.total_ms)
            _OS_REQUESTS.labels(status="ok").inc()
            return ticket_start_result

        # ── Fast-Path CRUD (CRUD tool de teste, agents/tickets/crud_tool.py) ──
        if decision.rota == "CRUD":
            from src.agents.tickets.crud_tool import start_crud_update
            crud_start_result = await start_crud_update(decision, message, session_id, user_context, r, t0)
            _OS_LATENCY.observe(crud_start_result.total_ms)
            _OS_REQUESTS.labels(status="ok").inc()
            return crud_start_result

        # ── 2. Planner ────────────────────────────────────────────────────────
        from src.application.chain.planner import criar_plano
        plan = await criar_plano(
            query=message,
            session_id=session_id,
            rota=decision.rota,
            dag_hint=decision.dag_hint,
            user_context=user_context,
            history=history,
            fatos=fatos,
        )

        # ── 3. Marca plano em andamento no Redis ──────────────────────────────
        await redis_state.mark_plan_processing(r, plan.plan_id)

        # ── 4. Despacha Workers via Celery Canvas (Não bloqueante!) ───────────
        await _despachar_workers(plan)

        # ── 5. Dispara aviso de latência com countdown de 3.0 segundos ───────
        from src.application.tasks.process_message_task import enviar_aviso_latencia_task
        chat_id = plan.context["user_context"].get("chat_id") or plan.session_id
        enviar_aviso_latencia_task.apply_async(
            args=[chat_id, plan.plan_id],
            countdown=3.0,
            queue="default"
        )

        ms = int((time.monotonic() - t0) * 1000)
        _OS_LATENCY.observe(ms)
        _OS_REQUESTS.labels(status="ok").inc()

        return OSResult(
            answer="",  # Resposta vazia pois será entregue assincronamente via Canvas callback
            plan_id=plan.plan_id,
            rota=decision.rota,
            cache_hit=False,
            total_ms=ms,
            status="ok",
        )

    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        _OS_LATENCY.observe(ms)
        _OS_REQUESTS.labels(status="error").inc()
        logger.exception("❌ [DISPATCHER] Falha: %s", exc)
        return OSResult(
            answer="Desculpe, tive um problema técnico. Tente novamente. 🙏",
            plan_id="",
            rota="GERAL",
            cache_hit=False,
            total_ms=ms,
            status="error",
            error=str(exc)[:200],
        )


async def _despachar_workers(plan) -> None:
    from celery import chord, chain
    from src.application.workers.worker_rag_search import worker_rag_search_task
    from src.application.workers.worker_synthesis import worker_synthesis_task
    from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
    from src.infrastructure.redis_client import get_redis_text
    _r = get_redis_text()

    def _hget_sync():
        return _r.hgetall(f"task_hist:{plan.session_id}")
    th = await asyncio.to_thread(_hget_sync)
    plan.context["task_history"] = dict(th) if th else {}

    rag_tasks = []
    synthesis_step = None
    other_step = None

    for step in plan.steps:
        worker_name = step["worker"]
        event_args = {
            "plan_id":      plan.plan_id,
            "session_id":   plan.session_id,
            "step_id":      step["id"],
            "depends_on":   step.get("depends_on", []),
            "plan_context": plan.context,
            "query":        plan.context.get("query", ""),
            **step.get("args", {}),
        }

        if worker_name == "rag_search":
            rag_tasks.append(worker_rag_search_task.s(event_args))
        elif worker_name == "synthesis":
            synthesis_step = step
        else:
            other_step = step

    delivery_ctx = {
        "plan_id": plan.plan_id,
        "chat_id": plan.context["user_context"].get("chat_id") or plan.session_id,
        "sender_jid": plan.session_id,
        "route": plan.rota,
        "query": plan.context.get("query", ""),
    }

    # Cenário A: Fluxo RAG clássico (RAG(s) -> Síntese -> Delivery)
    if rag_tasks and synthesis_step:
        synthesis_args = {
            "plan_id":      plan.plan_id,
            "session_id":   plan.session_id,
            "step_id":      synthesis_step["id"],
            "depends_on":   synthesis_step.get("depends_on", []),
            "plan_context": plan.context,
            "query":        plan.context.get("query", ""),
            **synthesis_step.get("args", {}),
        }

        # Constrói o fluxo Canvas: chord de RAGs -> Synthesis | WhatsApp Delivery
        workflow = chord(
            rag_tasks,
            worker_synthesis_task.s(synthesis_args) | enviar_resposta_whatsapp_task.s(delivery_ctx)
        )
        workflow.apply_async()
        logger.info("📤 [DISPATCHER] Canvas Chord disparado para plan=%s", plan.plan_id[:8])

    # Cenário B: Outros workers sem RAG (ex: greeting, action, etc.)
    elif other_step:
        event_args = {
            "plan_id":      plan.plan_id,
            "session_id":   plan.session_id,
            "step_id":      other_step["id"],
            "depends_on":   other_step.get("depends_on", []),
            "plan_context": plan.context,
            "query":        plan.context.get("query", ""),
            **other_step.get("args", {}),
        }

        # Resolve a assinatura do worker dinamicamente a partir do registry
        from src.application.workers.registry import _REGISTRY, _autodiscover_workers
        _autodiscover_workers()
        fn = _REGISTRY.get(other_step["worker"])
        if fn:
            workflow = chain(
                fn.s(event_args),
                enviar_resposta_whatsapp_task.s(delivery_ctx)
            )
            workflow.apply_async()
            logger.info("📤 [DISPATCHER] Canvas Chain disparado para worker=%s plan=%s",
                        other_step["worker"], plan.plan_id[:8])
        else:
            logger.error("❌ [DISPATCHER] Falha ao localizar worker %s no registry", other_step["worker"])
    else:
        logger.error("❌ [DISPATCHER] Plano inválido ou vazio para plan=%s", plan.plan_id)


_STT_TIMEOUT_S = 20.0   # Celery pickup (queue=media) + chamada Gemini + polling
_MAX_AUDIO_MB  = 16     # mesmo cap de _MAX_ENVIO_MB em worker_media_download.py

# Gatilho opt-in pra resposta sair também em áudio (Fase 3 do roadmap
# multimodal) — verbo "mandar/em/por (forma de) áudio". Deliberadamente não é
# o padrão automático: TTS ainda é caro (~15s de cold-load na 1ª chamada por
# processo worker) e nem toda resposta faz sentido em voz. Limitação
# conhecida: só detecta o pedido no TEXTO digitado (legenda/mensagem) — se o
# pedido for falado DENTRO de uma nota de voz, não é capturado aqui (checagem
# roda sobre o texto bruto recebido, antes/independente da transcrição STT).
_RE_AUDIO_REPLY = re.compile(
    r'\b(em|por|de)\s+(forma\s+de\s+)?áudio\b|\bmand(a|ar|e|em)\s+(um\s+|uma\s+mensagem\s+de\s+)?áudio\b',
    re.I,
)


def _quer_resposta_em_audio(text: str) -> bool:
    """True se o usuário pediu explicitamente a resposta em áudio."""
    return bool(_RE_AUDIO_REPLY.search(text or ""))


def _remover_pedido_audio(text: str) -> str:
    """
    Remove a frase-gatilho ("em áudio", "manda um áudio"...) do texto antes
    de virar `message` pro RAG/orchestrator/synthesis.

    Bug real de produção encontrado testando ao vivo: sem isso, o LLM de
    síntese via a frase completa ("Me explique em áudio sobre o Office 365")
    e respondia SOBRE o pedido de áudio ("não consigo te explicar em áudio,
    sou um assistente de texto") em vez de responder a pergunta de verdade —
    a frase-gatilho é sinal só pro roteamento de ENTREGA (`_quer_resposta_em_audio`,
    checado à parte sobre o texto original), não faz parte da pergunta em si.
    """
    limpo = _RE_AUDIO_REPLY.sub("", text or "")
    limpo = re.sub(r"\s{2,}", " ", limpo).strip(" ,.")
    return limpo or text


async def _transcrever_audio_recebido(r, user_context: dict, session_id: str) -> str | None:
    """
    Baixa o áudio recebido via Evolution API e despacha `worker_audio_to_text`
    (queue=media) — mantém o worker `default` (CELERY_CONCURRENCY=1) livre
    enquanto a transcrição roda, em vez de chamar o STT inline aqui. Faz
    polling em `plan:results:{plan_id}:{step_id}`, o mesmo Redis que o worker
    já escreve. Retorna None em qualquer falha (download vazio, áudio grande
    demais, timeout, erro de STT) — quem chama decide a mensagem de erro.
    """
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
    from src.application.workers.registry import dispatch as worker_dispatch
    from src.capabilities.persistence.redis_state import get_result_cache
    from src.infrastructure.observability.metrics import get_metrics
    from src.infrastructure.settings import settings

    msg_key_id = user_context.get("msg_key_id", "")
    t_stt      = time.monotonic()
    metrics    = get_metrics()

    def _falhar() -> None:
        metrics.observe_stt(settings.STT_PROVIDER, int((time.monotonic() - t_stt) * 1000), False)

    gateway = EvolutionAdapter()
    audio_b64, mimetype, _filename = await gateway.baixar_midia_base64(msg_key_id)
    if not audio_b64:
        logger.warning("⚠️  [STT] Download de áudio vazio | msg_key_id=%s", msg_key_id[:20])
        _falhar()
        return None

    tamanho_mb = len(audio_b64) * 3 / 4 / (1024 * 1024)
    if tamanho_mb > _MAX_AUDIO_MB:
        logger.warning("⚠️  [STT] Áudio grande demais (%.1fMB > %dMB) | msg_key_id=%s",
                       tamanho_mb, _MAX_AUDIO_MB, msg_key_id[:20])
        _falhar()
        return None

    plan_id = f"fast_stt_{session_id[-6:]}_{int(time.time() * 1000)}"
    step_id = "s_stt"
    task_id = worker_dispatch("audio_to_text", {
        "plan_id": plan_id, "session_id": session_id, "step_id": step_id,
        "audio_b64": audio_b64, "mime_type": mimetype or "audio/ogg",
    })
    if task_id is None:
        _falhar()
        return None

    deadline = time.monotonic() + _STT_TIMEOUT_S
    payload  = None
    while time.monotonic() < deadline:
        payload = await get_result_cache(r, plan_id, step_id)
        if payload is not None:
            break
        await asyncio.sleep(POLL_INTERVAL_S)

    ms = int((time.monotonic() - t_stt) * 1000)
    ok = bool(payload and payload.get("status") == "ok" and payload.get("transcription"))
    metrics.observe_stt(settings.STT_PROVIDER, ms, ok)

    if not ok:
        logger.warning("⚠️  [STT] Falha ou timeout | plan=%s | payload=%s", plan_id, payload)
        return None
    return payload["transcription"]


async def _aguardar_resposta_final(plan_id: str, timeout: float) -> dict | None:
    """
    Faz polling no Redis Stream, mas verifica primeiro se a resposta já está lá (Catch-up).
    Usado pelo modo síncrono de debug (admin hub / eval_api) — o fluxo WhatsApp
    normal não espera aqui, recebe a resposta via `enviar_resposta_whatsapp_task`.
    """
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

    deadline = time.monotonic() + timeout

    # 1. CATCH-UP: Verifica se o worker (ou greeting) já escreveu a resposta
    # Vamos verificar tanto s1 (saudações/simples) quanto s2 (síntese)
    for step in ["s1", "s2"]:
        data = await redis_state.get_result_cache(r, plan_id, step)
        if data and data.get("answer"):
            return {"answer": data["answer"], "action_buttons": data.get("action_buttons", []), "status": data.get("status", "ok")}

    # 2. POLLING: Se não achou de primeira, escuta o stream
    last_id = "0"  # Começa do zero para pegar o que acabou de ser escrito
    while time.monotonic() < deadline:
        try:
            # Sem block para não travar o loop de eventos (asyncio)
            results = await asyncio.to_thread(r.xread, {STREAM_FINAL_RESPONSES: last_id}, count=10)
            if results:
                for _stream_key, messages in results:
                    for msg_id, fields in messages:
                        f = {k.decode() if isinstance(k, bytes) else k:
                             v.decode() if isinstance(v, bytes) else v
                             for k, v in fields.items()}

                        if f.get("plan_id") == plan_id and f.get("status") in ("ok", "hitl_pending"):
                            btns = []
                            try:
                                if f.get("action_buttons"):
                                    btns = json.loads(f["action_buttons"])
                            except Exception:
                                pass
                            return {"answer": f.get("answer", ""), "status": f.get("status"), "action_buttons": btns}
                        last_id = msg_id
            await asyncio.sleep(POLL_INTERVAL_S)
        except Exception as e:
            logger.debug("Stream poll falhou: %s", e)
            await asyncio.sleep(POLL_INTERVAL_S)

    return None


def _buscar_resposta_cached(decision) -> str | None:
    """
    Quando há cache HIT, o cache contém o JSON da rota, não a resposta completa.
    Retorna None para forçar o pipeline (a resposta em si não está em cache aqui).
    O SemanticCache do projeto guarda apenas rotas+confiança, não respostas.
    """
    return None
