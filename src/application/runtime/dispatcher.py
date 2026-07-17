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

    # ── Guardrails (Entrada) ──────────────────────────────────────────────────
    from src.application.chain.guardrails import get_input_guardrail
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

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
            decision.rota = decision_rota

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
            url = urls[0] if urls else message

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
