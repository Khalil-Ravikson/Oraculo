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

    try:
        # ── 0. HITL Interception ──────────────────────────────────────────────────
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        hitl_state_raw = r.get(f"hitl:session:{session_id}")
        if hitl_state_raw:
            try:
                hitl_state = json.loads(hitl_state_raw if isinstance(hitl_state_raw, str) else hitl_state_raw.decode())
                msg_lower = message.strip().lower()
                
                if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
                    r.delete(f"hitl:session:{session_id}")
                    action = hitl_state.get("action")
                    
                    if action == "media_download":
                        from src.application.workers.registry import dispatch
                        dispatch(hitl_state.get("worker_name"), hitl_state.get("event"))
                        return OSResult(
                            answer="✅ Download confirmado! Enviado para processamento.",
                            plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="ok"
                        )
                    
                    return OSResult(
                        answer=f"✅ Ação '{action}' confirmada e iniciada.",
                        plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="ok"
                    )
                    
                elif msg_lower in ("nao", "não", "n", "no", "cancela", "cancelar"):
                    r.delete(f"hitl:session:{session_id}")
                    return OSResult(
                        answer="❌ Ação cancelada.",
                        plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="ok"
                    )
                else:
                    return OSResult(
                        answer="⚠️ Não entendi. Responda *SIM* para confirmar ou *NÃO* para cancelar.",
                        plan_id="hitl_fast_path", rota="HITL", cache_hit=True, total_ms=10, status="ok"
                    )
            except Exception as e:
                logger.error("Erro no parse do HITL state: %s", e)
                r.delete(f"hitl:session:{session_id}")

        # ── 1. Router ──────────────────────────────────────────────────────────
        from src.application.routing.semantic_router import rotear
        decision = await rotear(message, session_id, user_context)

        # Cache HIT: resposta imediata sem acionar workers
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

        # ── Fast-Path GREETING ────────────────────────────────────────────────
        if decision.rota == "GREETING":
            import random
            saudacoes = [
                "Olá! 😊 Sou o Oráculo UEMA. Como posso ajudar?",
                "Oi! Em que posso ajudá-lo(a) hoje?",
                "Olá! Pode perguntar sobre calendário, editais, contatos ou suporte. 🎓",
            ]
            ms = int((time.monotonic() - t0) * 1000)
            _OS_LATENCY.observe(ms)
            _OS_REQUESTS.labels(status="ok").inc()
            return OSResult(
                answer=random.choice(saudacoes),
                plan_id="fast_greeting",
                rota=decision.rota,
                cache_hit=False,
                total_ms=ms,
                status="ok"
            )

        # ── Fast-Path MEDIA_DOWNLOAD HITL ─────────────────────────────────────
        if decision.rota == "MEDIA_DOWNLOAD":
            url = decision.dag_hint.get("url", message)
            
            # Salvar intenção de HITL no Redis
            hitl_state = {
                "action": "media_download",
                "worker_name": decision.dag_hint["steps"][0],
                "event": {
                    "plan_id": "fast_media",
                    "session_id": session_id,
                    "step_id": "s1",
                    "url": url,
                    "hitl_confirmed": True,
                }
            }
            # Reusa o 'r' já instanciado no bloco de HITL Interception
            r.setex(f"hitl:session:{session_id}", 300, json.dumps(hitl_state, ensure_ascii=False))

            ms = int((time.monotonic() - t0) * 1000)
            _OS_LATENCY.observe(ms)
            _OS_REQUESTS.labels(status="ok").inc()

            return OSResult(
                answer="🎥 **Mídia detectada!**\n\nIdentifiquei um link suportado.\nDeseja iniciar o download deste arquivo agora?",
                plan_id="fast_media",
                rota=decision.rota,
                cache_hit=False,
                total_ms=ms,
                status="hitl_pending",
                action_buttons=[{"label": "Sim, baixar", "value": "sim"}, {"label": "Não", "value": "nao"}]
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
        r.setex(f"plan:status:{plan.plan_id}", 120, "processing")

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
        raw = r.get(key)
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
            results = r.xread({STREAM_FINAL_RESPONSES: last_id}, count=10)
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