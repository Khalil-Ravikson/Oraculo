"""
src/application/workers/worker_synthesis.py
============================================
Worker Synthesis — espera as dependências do DAG, junta os resultados
dos steps anteriores e usa Gemini Pro para gerar a resposta final.

FLUXO:
  1. Recebe evento com plan_id + step_id
  2. Aguarda todos os steps em depends_on estarem completos no Redis
  3. Junta os chunks de todos os RAG searches
  4. Chama Gemini Pro para síntese final
  5. Publica resultado no Stream de respostas

STREAM:
  Consome resultados de: oraculo:stream:step_results
  Publica resposta em:   oraculo:stream:final_responses

MÉTRICAS:
  oraculo_synthesis_gemini_pro_tokens_total{direction}
  oraculo_synthesis_latency_ms
  oraculo_event_latency_ms{worker="synthesis"}
"""
from __future__ import annotations

import json
import logging
import time

from prometheus_client import Counter, Histogram

from src.application.workers.registry import register
from src.infrastructure.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Métricas ──────────────────────────────────────────────────────────────────
_PRO_TOKENS = Counter(
    "oraculo_synthesis_gemini_pro_tokens_total",
    "Tokens do Gemini Pro no Synthesis Worker",
    ["direction"],
)
_SYNTH_LATENCY = Histogram(
    "oraculo_synthesis_latency_ms",
    "Latência do Synthesis em ms",
    buckets=[100, 250, 500, 1000, 2000, 4000],
)
_EVENT_LATENCY = Histogram(
    "oraculo_synthesis_event_latency_ms",
    "Latência ponta-a-ponta do evento",
    ["worker"],
    buckets=[50, 100, 250, 500, 1000, 2000, 5000],
)

# ── Stream/Redis keys ─────────────────────────────────────────────────────────
STREAM_STEP_RESULTS    = "oraculo:stream:step_results"
STREAM_FINAL_RESPONSES = "oraculo:stream:final_responses"
RESULTS_CACHE_PREFIX   = "plan:results:"
RESULTS_TTL            = 120   # segundos — planos expiram após 2min
STREAM_MAXLEN          = 2_000
MAX_WAIT_SECONDS       = 12    # timeout aguardando dependências
POLL_INTERVAL          = 0.25  # segundos entre polls

@register("synthesis")
@celery_app.task(
    name="worker_synthesis",
    bind=True,
    max_retries=2,
    queue="synthesis",
)
def worker_synthesis_task(self, *args, **kwargs) -> dict:
    import asyncio
    # Suporta assinatura clássica task(event) e assinatura Canvas task(results, event)
    if len(args) == 2:
        results, event = args
    elif len(args) == 1:
        if isinstance(args[0], list):
            results = args[0]
            event = kwargs.get("event") or {}
        else:
            results = []
            event = args[0]
    else:
        results = []
        event = kwargs.get("event") or {}

    return asyncio.run(_run_async(results, event))


async def _run_async(results: list, event: dict) -> dict:
    """
    Função principal async do worker de síntese.
    """
    t_start = time.monotonic()

    plan_id     = event.get("plan_id", "")
    session_id  = event.get("session_id", "")
    step_id     = event.get("step_id", "s2")
    depends_on  = event.get("depends_on", [])
    plan_ctx    = event.get("plan_context", {})
    args        = event.get("args", {})
    max_tokens  = int(args.get("max_tokens", 512))
    stream_id   = event.get("stream_id", "")

    logger.info("🧠 [SYNTH WORKER] Iniciando | plan=%s step=%s deps=%s | results=%d",
                plan_id[:8], step_id, depends_on, len(results))

    todos_chunks = []

    # 1. Se resultados do chord do Celery foram fornecidos diretamente, extrai os chunks deles
    if results:
        for r_data in results:
            if isinstance(r_data, dict):
                chunks = r_data.get("chunks", [])
                todos_chunks.extend(chunks)
    else:
        # 2. Fallback: Aguarda dependências no Redis (caso chamado de forma isolada / testes antigos)
        step_results = await _aguardar_dependencias_async(plan_id, depends_on)

        if step_results is None:
            ms = int((time.monotonic() - t_start) * 1000)
            logger.error("❌ [SYNTH WORKER] Timeout aguardando deps do plan=%s", plan_id[:8])
            _publicar_resposta(
                plan_id=plan_id, session_id=session_id,
                resposta="Timeout interno. Tente novamente. 🙏",
                status="timeout", latency_ms=ms,
            )
            return {"status": "timeout", "plan_id": plan_id, "answer": "Timeout interno."}

        for step_id_dep, result_data in step_results.items():
            chunks = result_data.get("chunks", [])
            todos_chunks.extend(chunks)

    # Deduplicação e ordenação por score
    vistos = set()
    chunks_unicos = []
    for c in sorted(todos_chunks, key=lambda x: x.get("rrf_score", 0), reverse=True):
        fp = c.get("content", "")[:80].strip().lower()
        if fp and fp not in vistos:
            vistos.add(fp)
            chunks_unicos.append(c)

    logger.info("📦 [SYNTH WORKER] %d chunks únicos para síntese", len(chunks_unicos))

    # ── Gera resposta via SynthesisService (agents/academic_knowledge/synthesis.py) ──
    from src.agents.academic_knowledge.synthesis import SynthesisService
    synth_result = await SynthesisService().sintetizar(
        chunks=chunks_unicos[:6],
        plan_ctx=plan_ctx,
        max_tokens=max_tokens,
    )
    if synth_result.error:
        logger.exception("❌ [SYNTH WORKER] Pro falhou: %s", synth_result.error)
        resposta = "Estou enfrentando lentidão, mas anotei sua dúvida. Tente novamente em alguns instantes. 🙏"
        status = "error"
    else:
        resposta = synth_result.answer
        status = "ok"
        if synth_result.tokens_in or synth_result.tokens_out:
            _PRO_TOKENS.labels(direction="input").inc(synth_result.tokens_in)
            _PRO_TOKENS.labels(direction="output").inc(synth_result.tokens_out)

    ms = int((time.monotonic() - t_start) * 1000)
    _SYNTH_LATENCY.observe(ms)
    _EVENT_LATENCY.labels(worker="synthesis").observe(ms)

    _publicar_resposta(
        plan_id=plan_id, session_id=session_id,
        resposta=resposta, status=status, latency_ms=ms,
    )

    # Salva a resposta também para o pool saber que o plan terminou
    _marcar_step_completo(plan_id, step_id, {"answer": resposta, "status": status})

    # Salva resultado na Layer 3 (Task History)
    try:
        from src.infrastructure.redis_client import get_redis_text
        _r = get_redis_text()
        _r.hset(f"task_hist:{session_id}", mapping={
            "last_worker": "synthesis",
            "last_result": resposta[:400],
            "ts": str(int(time.time())),
        })
        _r.expire(f"task_hist:{session_id}", 1800)
    except Exception:
        pass

    logger.info("✅ [SYNTH WORKER] Resposta gerada | %d chars | %dms", len(resposta), ms)
    
    # Salva no Semantic Cache se for uma rota passível de cache
    if status == "ok" and plan_ctx.get("query"):
        try:
            from src.infrastructure.semantic_cache import SemanticCache
            import asyncio
            sc = SemanticCache(threshold=0.92)
            # Como a síntese é chamada pelo planner, podemos não ter a rota no plan_ctx diretamente
            # Vamos salvar na rota indicada, ou GERAL
            rota_efetiva = plan_ctx.get("route", plan_ctx.get("route_hint", "GERAL"))
            
            # Não fazemos cache semântico de rotas de integração (SIGAA, MEDIA)
            if rota_efetiva not in ("SIGAA", "MEDIA_DOWNLOAD", "GREETING"):
                # Não podemos dar await diretamente se não quisermos bloquear
                asyncio.create_task(
                    sc.set(
                        query=plan_ctx["query"],
                        rota=rota_efetiva,
                        response={"answer": resposta, "status": "ok"},
                        ttl=3600
                    )
                )
        except Exception as e:
            logger.warning("⚠️ Falha ao salvar no SemanticCache: %s", e)

    return {"status": status, "plan_id": plan_id, "answer": resposta, "chars": len(resposta), "latency_ms": ms}


async def _aguardar_dependencias_async(
    plan_id: str,
    depends_on: list[str],
    timeout: float = MAX_WAIT_SECONDS,
) -> dict | None:
    """
    Aguarda todos os steps dependentes estarem no Redis sem bloquear a thread.
    """
    if not depends_on:
        return {}

    import time as _time
    import asyncio
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

    deadline = _time.monotonic() + timeout
    resultados = {}

    while _time.monotonic() < deadline:
        for dep_step in depends_on:
            if dep_step in resultados:
                continue
            key = f"{RESULTS_CACHE_PREFIX}{plan_id}:{dep_step}"
            raw = r.get(key)
            if raw:
                try:
                    resultados[dep_step] = json.loads(raw if isinstance(raw, str) else raw.decode())
                except Exception:
                    pass

        if len(resultados) >= len(depends_on):
            return resultados

        await asyncio.sleep(POLL_INTERVAL)

    if resultados:
        logger.warning("⚠️  [SYNTH WORKER] Timeout parcial: %d/%d deps",
                       len(resultados), len(depends_on))
        return resultados
    return None


def _marcar_step_completo(plan_id: str, step_id: str, data: dict) -> None:
    """Salva o resultado do step no Redis para outros steps dependentes."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        key = f"{RESULTS_CACHE_PREFIX}{plan_id}:{step_id}"
        r.setex(key, RESULTS_TTL, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.warning("⚠️  [SYNTH WORKER] Falha ao marcar step completo: %s", e)


def _publicar_resposta(
    plan_id: str,
    session_id: str,
    resposta: str,
    status: str,
    latency_ms: int,
) -> None:
    """Publica a resposta final no Stream para o FastAPI/Celery entregar ao usuário."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.xadd(
            STREAM_FINAL_RESPONSES,
            {
                "plan_id":    plan_id,
                "session_id": session_id,
                "status":     status,
                "answer":     resposta[:4000],
                "latency_ms": str(latency_ms),
                "ts":         str(time.time()),
            },
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    except Exception as e:
        logger.error("❌ [SYNTH WORKER] Falha ao publicar resposta final: %s", e)