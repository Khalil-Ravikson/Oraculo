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
    status: str   # "ok" | "timeout" | "error"
    error: str = ""


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

        # ── 3. Despacha Workers (Celery) ───────────────────────────────────────
        await _despachar_workers(plan)

        # ── 4. Aguarda resposta final ─────────────────────────────────────────
        answer = await _aguardar_resposta_final(plan.plan_id, timeout=RESPONSE_TIMEOUT_S)

        if answer is None:
            # Timeout: salva estado pendente e retorna mensagem de espera
            _salvar_plan_pendente(plan)
            ms = int((time.monotonic() - t0) * 1000)
            _OS_LATENCY.observe(ms)
            _OS_REQUESTS.labels(status="timeout").inc()
            return OSResult(
                answer="⏳ Sua pergunta está sendo processada. Respondo em instantes!",
                plan_id=plan.plan_id,
                rota=decision.rota,
                cache_hit=False,
                total_ms=ms,
                status="timeout",
            )

        ms = int((time.monotonic() - t0) * 1000)
        _OS_LATENCY.observe(ms)
        _OS_REQUESTS.labels(status="ok").inc()

        return OSResult(
            answer=answer,
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
    """
    Despacha cada step do DAG para o Celery respeitando a ordem de dependências.
    Steps sem dependências são despachados imediatamente (em paralelo).
    Steps com dependências são despachados após armazenar contexto no Redis.
    """
    from src.application.workers.worker_rag_search import worker_rag_search_task
    from src.application.workers.worker_synthesis import worker_synthesis_task

    plan_dict = plan.to_dict()

    for step in plan.steps:
        step_id   = step["id"]
        worker    = step["worker"]
        args      = step.get("args", {})
        depends_on = step.get("depends_on", [])

        event = {
            "plan_id":      plan.plan_id,
            "session_id":   plan.session_id,
            "step_id":      step_id,
            "depends_on":   depends_on,
            "plan_context": plan.context,
            **args,
            # query sempre presente para os workers
            "query": plan.context.get("query", ""),
        }

        if worker == "rag_search":
            # Despacha imediatamente (sem dependências em geral)
            worker_rag_search_task.apply_async(args=[event], queue="rag_search")
            logger.debug("📤 [OS] Despachado rag_search step=%s plan=%s", step_id, plan.plan_id[:8])

        elif worker == "synthesis":
            # Synthesis aguarda internamente as deps via polling Redis
            worker_synthesis_task.apply_async(args=[event], queue="synthesis")
            logger.debug("📤 [OS] Despachado synthesis step=%s plan=%s", step_id, plan.plan_id[:8])

        elif worker == "greeting":
            # Greeting é rápido — executa inline sem Celery
            _executar_greeting(plan)

        elif worker == "crud_confirm":
            # HITL — salva no Redis e retorna mensagem de confirmação
            _iniciar_crud_hitl(plan, args)

async def _aguardar_resposta_final(plan_id: str, timeout: float) -> str | None:
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
                    return data["answer"]
            except Exception:
                pass

    # 2. POLLING: Se não achou de primeira, escuta o stream
    last_id = "0"  # Começa do zero para pegar o que acabou de ser escrito
    while time.monotonic() < deadline:
        try:
            # block=200ms evita travar a thread
            results = r.xread({STREAM_FINAL_RESPONSES: last_id}, count=10, block=200)
            if results:
                for _stream_key, messages in results:
                    for msg_id, fields in messages:
                        f = {k.decode() if isinstance(k, bytes) else k:
                             v.decode() if isinstance(v, bytes) else v
                             for k, v in fields.items()}
                        
                        if f.get("plan_id") == plan_id and f.get("status") == "ok":
                            return f.get("answer", "")
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
        r.setex(f"hitl:{plan.session_id}", 300, json.dumps(hitl_data, ensure_ascii=False))

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