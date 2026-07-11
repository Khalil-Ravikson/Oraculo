"""
src/application/chain/cognitive_os.py
=======================================
CognitiveOS — Substitui o OracleChain monolítico.

Orquestra o pipeline orientado a eventos:
  1. SemanticRouter → classifica e retorna do cache se possível
  2. Planner → gera DAG de execução
  3. Despacha workers via Celery (desacoplado)
  4. Aguarda resposta final no Redis Stream

Para requests síncronos (webhook WhatsApp):
  Modo "fast": aguarda a resposta com timeout de 15s.
  Se timeout: enfileira e responde "Aguarde...".

MÉTRICAS:
  oraculo_cognitive_os_latency_ms (histogram)
  oraculo_cognitive_os_requests_total{status}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from prometheus_client import Counter, Histogram

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

# Stream key de respostas finais
STREAM_FINAL_RESPONSES = "oraculo:stream:final_responses"
RESULTS_CACHE_PREFIX   = "plan:results:"
RESULTS_TTL            = 120

# Timeout máximo esperando resposta do pipeline
RESPONSE_TIMEOUT_S = 15.0
POLL_INTERVAL_S    = 0.2


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
    Entry point do CognitiveOS.
    Substitui OracleChain.invoke().
    """
    t0 = time.monotonic()
    fatos = fatos or []

    # ── Guardrails (Entrada) ──────────────────────────────────────────────────
    from src.application.chain.guardrails import get_input_guardrail
    from src.infrastructure.redis_client import get_redis_text
    r_text = get_redis_text()
    
    def _validate_sync():
        return get_input_guardrail().validate(message, session_id, r_text)
        
    ok, text_or_error = await asyncio.to_thread(_validate_sync)
    if not ok:
        return OSResult(answer=text_or_error, plan_id="", rota="BLOCKED",
                        cache_hit=False, total_ms=0, status="error")
    message = text_or_error  # sanitizado

    try:
        # ── 0. HITL Interception ──────────────────────────────────────────────────
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        hitl_state_raw = await asyncio.to_thread(r.get, f"hitl:session:{session_id}")
        if hitl_state_raw:
            try:
                hitl_state = json.loads(hitl_state_raw if isinstance(hitl_state_raw, str) else hitl_state_raw.decode())
                action = hitl_state.get("action")
                msg_clean = message.strip()
                
                if action == "sigaa_collect_cpf":
                    # Limpa e valida CPF
                    cpf = re.sub(r"\D", "", msg_clean)
                    if len(cpf) != 11:
                        # IMPORTANTE: NÃO deletar o estado HITL aqui.
                        return OSResult(
                            answer="❌ **CPF Inválido!**\nO CPF deve conter exatamente 11 dígitos numéricos. Por favor, informe seu CPF novamente:",
                            plan_id=hitl_state.get("event", {}).get("plan_id", "sigaa_auth"),
                            rota="SIGAA",
                            cache_hit=False,
                            total_ms=10,
                            status="hitl_pending"
                        )
                    # Avança para coletar senha
                    hitl_state["action"] = "sigaa_collect_password"
                    hitl_state["cpf"] = cpf
                    await asyncio.to_thread(r.setex, f"hitl:session:{session_id}", 300, json.dumps(hitl_state, ensure_ascii=False))
                    return OSResult(
                        answer="🔐 **CPF recebido!**\nAgora, por favor, envie sua **senha do SIGAA** para iniciarmos o acesso (sua senha é transmitida de forma segura e não será salva persistentemente):",
                        plan_id=hitl_state.get("event", {}).get("plan_id", "sigaa_auth"),
                        rota="SIGAA",
                        cache_hit=False,
                        total_ms=10,
                        status="hitl_pending"
                    )
                
                elif action == "sigaa_collect_password":
                    senha = msg_clean
                    cpf = hitl_state.get("cpf")
                    target_action = hitl_state.get("target_action")
                    event = hitl_state.get("event", {})
                    
                    # Anexa credenciais ao event de forma temporária usando um token
                    event["login"] = cpf
                    import uuid
                    auth_token = str(uuid.uuid4())
                    await asyncio.to_thread(r.setex, f"hitl:auth_token:{auth_token}", 300, json.dumps({"senha": senha}))
                    event["auth_token"] = auth_token
                    event["hitl_confirmed"] = True
                    
                    await asyncio.to_thread(r.delete, f"hitl:session:{session_id}")
                    
                    from src.application.workers.registry import _REGISTRY, _autodiscover_workers
                    from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
                    from celery import chain
                    
                    _autodiscover_workers()
                    fn = _REGISTRY.get(target_action)
                    if fn:
                        delivery_ctx = {
                            "plan_id": event.get("plan_id", "hitl_fast_path"),
                            "chat_id": user_context.get("chat_id") or session_id,
                            "sender_jid": session_id,
                            "route": "SIGAA",
                            "query": event.get("query", ""),
                        }
                        workflow = chain(
                            fn.s(event),
                            enviar_resposta_whatsapp_task.s(delivery_ctx)
                        )
                        workflow.apply_async()
                        
                        desc = hitl_state.get("description", target_action)
                        return OSResult(
                            answer=f"🚀 **Autenticação em andamento!**\nIniciando acesso seguro ao SIGAA para a operação **{desc}**. Você receberá os resultados em breve por aqui.",
                            plan_id=event.get("plan_id", "hitl_fast_path"),
                            rota="SIGAA",
                            cache_hit=True,
                            total_ms=10,
                            status="ok"
                        )
                    else:
                        from src.application.workers.registry import dispatch
                        dispatch(target_action, event)
                        return OSResult(
                            answer=f"✅ Ação '{target_action}' iniciada.",
                            plan_id=event.get("plan_id", "hitl_fast_path"),
                            rota="SIGAA",
                            cache_hit=True,
                            total_ms=10,
                            status="ok"
                        )
                
                # Fallback para confirmações SIM/NAO legadas (ex: baixar mídia)
                msg_lower = msg_clean.lower()
                if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
                    await asyncio.to_thread(r.delete, f"hitl:session:{session_id}")
                    action = hitl_state.get("action")
                    worker_name = hitl_state.get("worker_name")
                    event = hitl_state.get("event", {})
                    
                    from src.application.workers.registry import _REGISTRY, _autodiscover_workers
                    from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
                    from celery import chain
                    
                    _autodiscover_workers()
                    fn = _REGISTRY.get(worker_name)
                    if fn:
                        delivery_ctx = {
                            "plan_id": event.get("plan_id", "hitl_fast_path"),
                            "chat_id": user_context.get("chat_id") or session_id,
                            "sender_jid": session_id,
                            "route": "SIGAA" if (action and (action.startswith("sigaa_") or "sigaa" in worker_name)) else "MEDIA_DOWNLOAD",
                            "query": event.get("query", ""),
                        }
                        workflow = chain(
                            fn.s(event),
                            enviar_resposta_whatsapp_task.s(delivery_ctx)
                        )
                        workflow.apply_async()
                        
                        desc = hitl_state.get("description", action)
                        return OSResult(
                            answer=f"✅ Ação **{desc}** confirmada! Enviada para processamento no servidor Celery.",
                            plan_id=event.get("plan_id", "hitl_fast_path"),
                            rota="SIGAA" if (action and (action.startswith("sigaa_") or "sigaa" in worker_name)) else "MEDIA_DOWNLOAD",
                            cache_hit=True,
                            total_ms=10,
                            status="ok"
                        )
                elif msg_lower in ("nao", "não", "n", "no", "cancela", "cancelar"):
                    await asyncio.to_thread(r.delete, f"hitl:session:{session_id}")
                    return OSResult(
                        answer="❌ Ação cancelada.",
                        plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="ok"
                    )
                else:
                    return OSResult(
                        answer="⚠️ Não entendi. Responda *SIM* para confirmar ou *NÃO* para cancelar.",
                        plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="hitl_pending"
                    )
            except Exception as e:
                logger.error("Erro no parse do HITL state: %s", e)
                # Não deletamos a sessão em caso de erro para permitir nova tentativa do usuário


        # ── 0b. Fast Path: comandos explícitos ───────────────────────────────────────
        # ! @ $ → vai direto pro router semântico existente (sem gastar tokens no LLM)
        # linguagem natural → LLMOrchestrator decide a ação
        is_command = message.startswith(("!", "@", "$"))

        if not is_command:
            from src.application.routing.llm_orchestrator import orchestrate
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
            decision_rota = None  # deixa o Semantic Router decidir

        # ── 1. Router (semântico, só para comandos ou quando o Orchestrator pediu RAG) ──
        from src.application.routing.semantic_router import rotear
        decision = await rotear(message, session_id, user_context)

        # Orchestrator tem prioridade sobre o router semântico para linguagem natural
        if not is_command and decision_rota:
            decision.rota = decision_rota

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
            resposta = random.choice(saudacoes)
            
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
            # Extrair URL da mensagem (se houver)
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

        # ── Fast-Path SIGAA HITL ──────────────────────────────────────────────
        if decision.rota == "SIGAA":
            from src.application.use_cases.sigaa_use_cases import SIGAAUseCase
            uc = SIGAAUseCase()
            fluxo = uc.detectar_fluxo(message)
            worker = fluxo["worker"] if fluxo else "sigaa_biblioteca"
            args = fluxo["args"] if fluxo else {}
            
            # Se já está confirmado via HITL, prossegue normalmente
            if args.get("hitl_confirmed"):
                args.pop("hitl_confirmed", None)
            else:
                friendly_names = {
                    "sigaa_notas": "Consultar Minhas Notas",
                    "sigaa_indice": "Consultar Índice Acadêmico (CR)",
                    "sigaa_historico": "Emitir Histórico Escolar",
                    "sigaa_estrutura": "Consultar Estrutura Curricular",
                    "sigaa_turmas": "Consultar Turmas do Semestre",
                    "sigaa_calendario": "Consultar Calendário Acadêmico",
                    "sigaa_extensao": "Realizar Inscrição em Evento de Extensão",
                    "sigaa_biblioteca": "Consultar Acervo da Biblioteca",
                    "sigaa_processos": "Consultar Processos Seletivos",
                }
                op_desc = friendly_names.get(worker, "Acessar o Portal SIGAA")
                
                # Se o usuário já possui cookies de sessão válidos no Redis, dispacha a tarefa imediatamente
                session_key = f"sigaa:session:{session_id}"
                if await asyncio.to_thread(r.exists, session_key):
                    from src.application.workers.registry import _autodiscover_workers, _REGISTRY
                    from src.application.tasks.process_message_task import enviar_resposta_whatsapp_task
                    from celery import chain
                    
                    _autodiscover_workers()
                    fn = _REGISTRY.get(worker)
                    if fn:
                        event = {
                            "plan_id": f"fast_{worker}_{int(time.time())}",
                            "session_id": session_id,
                            "step_id": "s1",
                            "query": message,
                            **args
                        }
                        delivery_ctx = {
                            "plan_id": event["plan_id"],
                            "chat_id": user_context.get("chat_id") or session_id,
                            "sender_jid": session_id,
                            "route": "SIGAA",
                            "query": message,
                        }
                        workflow = chain(
                            fn.s(event),
                            enviar_resposta_whatsapp_task.s(delivery_ctx)
                        )
                        workflow.apply_async()
                        
                        ms = int((time.monotonic() - t0) * 1000)
                        _OS_LATENCY.observe(ms)
                        _OS_REQUESTS.labels(status="ok").inc()
                        return OSResult(
                            answer="🔄 **Acessando dados no SIGAA...**\nUtilizando sua sessão ativa existente. Buscando informações, aguarde um instante...",
                            plan_id=event["plan_id"],
                            rota="SIGAA",
                            cache_hit=True,
                            total_ms=ms,
                            status="ok"
                        )
                
                # Caso contrário, inicia a coleta de CPF
                hitl_state = {
                    "action": "sigaa_collect_cpf",
                    "target_action": worker,
                    "description": op_desc,
                    "event": {
                        "plan_id": f"fast_{worker}_{int(time.time())}",
                        "session_id": session_id,
                        "step_id": "s1",
                        "query": message,
                        **args
                    }
                }
                await asyncio.to_thread(r.setex, f"hitl:session:{session_id}", 300, json.dumps(hitl_state, ensure_ascii=False))
                
                ms = int((time.monotonic() - t0) * 1000)
                _OS_LATENCY.observe(ms)
                _OS_REQUESTS.labels(status="ok").inc()
                
                return OSResult(
                    answer=f"⚠️ **Autenticação Requerida**\n\nPara executar a operação **{op_desc}**, preciso que você se autentique no SIGAA.\n\nPor favor, informe seu **CPF** (apenas números, sem pontos ou traços):",
                    plan_id=hitl_state["event"]["plan_id"],
                    rota=decision.rota,
                    cache_hit=False,
                    total_ms=ms,
                    status="hitl_pending",
                    action_buttons=[]
                )

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
        await asyncio.to_thread(r.setex, f"plan:status:{plan.plan_id}", 120, "processing")

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
        logger.exception("❌ [COGNITIVE OS] Falha: %s", exc)
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
    import json as _json
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
        logger.info("📤 [COGNITIVE OS] Canvas Chord disparado para plan=%s", plan.plan_id[:8])

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
            logger.info("📤 [COGNITIVE OS] Canvas Chain disparado para worker=%s plan=%s", 
                        other_step["worker"], plan.plan_id[:8])
        else:
            logger.error("❌ [COGNITIVE OS] Falha ao localizar worker %s no registry", other_step["worker"])
    else:
        logger.error("❌ [COGNITIVE OS] Plano inválido ou vazio para plan=%s", plan.plan_id)


        
async def _aguardar_resposta_final(plan_id: str, timeout: float) -> dict | None:
    """
    Faz polling no Redis Stream, mas verifica primeiro se a resposta já está lá (Catch-up).
    """
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

    deadline = time.monotonic() + timeout

    # 1. CATCH-UP: Verifica se o worker (ou greeting) já escreveu a resposta
    # Vamos verificar tanto s1 (saudações/simples) quanto s2 (síntese)
    for step in ["s1", "s2"]:
        key = f"{RESULTS_CACHE_PREFIX}{plan_id}:{step}"
        raw = await asyncio.to_thread(r.get, key)
        if raw:
            try:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                if data.get("answer"):
                    return {"answer": data["answer"], "action_buttons": data.get("action_buttons", []), "status": data.get("status", "ok")}
            except Exception:
                pass

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
    # Se o cache guardasse respostas completas, retornaria aqui.
    # Como guarda apenas a rota, retornamos None para processar normalmente.
    return None


def _executar_greeting(plan) -> None:
    """Salva resposta de greeting direto no Redis para o polling detectar."""
    import random
    saudacoes = [
        "Olá! 😊 Sou o Oráculo UEMA. Como posso ajudar?",
        "Oi! Em que posso ajudá-lo(a) hoje?",
        "Olá! Pode perguntar sobre calendário, editais, contatos ou suporte. 🎓",
    ]
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        key = f"{RESULTS_CACHE_PREFIX}{plan.plan_id}:s1"
        r.setex(key, RESULTS_TTL,
                json.dumps({"answer": random.choice(saudacoes), "status": "ok"},
                ensure_ascii=False))
        # Publica também no stream final
        r.xadd(
            STREAM_FINAL_RESPONSES,
            {"plan_id": plan.plan_id, "session_id": plan.session_id,
             "status": "ok", "answer": random.choice(saudacoes),
             "latency_ms": "1", "ts": str(time.time())},
            maxlen=2000, approximate=True,
        )
    except Exception as e:
        logger.warning("⚠️  Greeting inline falhou: %s", e)


def _iniciar_crud_hitl(plan, args: dict) -> None:
    """Inicia fluxo HITL para CRUD — salva no Redis para confirmação."""
    try:
        import time as _time
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        hitl_data = {
            "action":      args.get("action", "desconhecido"),
            "description": args.get("description", "operação"),
            "args":        args,
            "status":      "pending",
            "expires_at":  int(_time.time()) + 300,
            "chat_id":     plan.context["user_context"].get("chat_id"),
        }
        r.setex(f"hitl:session:{plan.session_id}", 300, json.dumps(hitl_data, ensure_ascii=False))

        # Publica mensagem de confirmação no stream final
        r.xadd(
            STREAM_FINAL_RESPONSES,
            {
                "plan_id":    plan.plan_id,
                "session_id": plan.session_id,
                "status":     "hitl_pending",
                "answer":     f"⚠️ *Confirmação necessária*\n\n{args.get('description','operação')}\n\nResponda *SIM* para confirmar ou *NÃO* para cancelar.",
                "latency_ms": "1",
                "ts":         str(_time.time()),
            },
            maxlen=2000, approximate=True,
        )
    except Exception as e:
        logger.warning("⚠️  CRUD HITL inline falhou: %s", e)


def _salvar_plan_pendente(plan) -> None:
    """Salva o plano no Redis para retomada futura (caso timeout)."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.setex(
            f"plan:pending:{plan.session_id}",
            300,
            json.dumps(plan.to_dict(), ensure_ascii=False),
        )
    except Exception:
        pass