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
MAX_WAIT_SECONDS       = 30    # timeout aguardando dependências
POLL_INTERVAL          = 0.25  # segundos entre polls

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_SYNTHESIS = """Você é o Oráculo, assistente oficial da UEMA via WhatsApp.
Responda APENAS com base nas informações fornecidas em <contexto_rag>.
Se a informação não estiver no contexto: diga "Não encontrei essa informação nos meus registros. Consulte uema.br."
NUNCA invente datas, números ou emails.
Use *negrito* para dados importantes. Máximo 3 parágrafos. Seja conciso."""

@register("synthesis")
@celery_app.task(
    name="worker_synthesis",
    bind=True,
    max_retries=2,
    queue="synthesis",
)
def worker_synthesis_task(self, event: dict) -> dict:
    """
    Task Celery que sintetiza a resposta final.

    Args:
        event: dict com plan_id, session_id, step_id, depends_on[],
               plan_context (query, user_context, history, fatos),
               args (max_tokens, tone?)
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

    logger.info("🧠 [SYNTH WORKER] Iniciando | plan=%s step=%s deps=%s",
                plan_id[:8], step_id, depends_on)

    # ── Aguarda dependências ───────────────────────────────────────────────────
    step_results = _aguardar_dependencias(plan_id, depends_on)

    if step_results is None:
        ms = int((time.monotonic() - t_start) * 1000)
        logger.error("❌ [SYNTH WORKER] Timeout aguardando deps do plan=%s", plan_id[:8])
        _publicar_resposta(
            plan_id=plan_id, session_id=session_id,
            resposta="Timeout interno. Tente novamente. 🙏",
            status="timeout", latency_ms=ms,
        )
        return {"status": "timeout", "plan_id": plan_id}

    # ── Junta chunks de todos os steps RAG ────────────────────────────────────
    todos_chunks = []
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

    # ── Gera resposta com Gemini Pro ───────────────────────────────────────────
    try:
        resposta = _sintetizar_com_pro(
            chunks=chunks_unicos[:6],
            plan_ctx=plan_ctx,
            max_tokens=max_tokens,
        )
        status = "ok"
    except Exception as exc:
        logger.exception("❌ [SYNTH WORKER] Pro falhou: %s", exc)
        resposta = "Tive dificuldades ao processar. Tente reformular sua pergunta. 🙏"
        status = "error"

    ms = int((time.monotonic() - t_start) * 1000)
    _SYNTH_LATENCY.observe(ms)
    _EVENT_LATENCY.labels(worker="synthesis").observe(ms)

    _publicar_resposta(
        plan_id=plan_id, session_id=session_id,
        resposta=resposta, status=status, latency_ms=ms,
    )

    # Salva a resposta também para o pool saber que o plan terminou
    _marcar_step_completo(plan_id, step_id, {"answer": resposta, "status": status})

    logger.info("✅ [SYNTH WORKER] Resposta gerada | %d chars | %dms", len(resposta), ms)
    return {"status": status, "plan_id": plan_id, "chars": len(resposta), "latency_ms": ms}


def _aguardar_dependencias(
    plan_id: str,
    depends_on: list[str],
    timeout: float = MAX_WAIT_SECONDS,
) -> dict | None:
    """
    Aguarda todos os steps dependentes estarem no Redis.
    Usa polling com sleep. Retorna {step_id: result_data} ou None se timeout.
    """
    if not depends_on:
        return {}

    import time as _time
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

        _time.sleep(POLL_INTERVAL)

    # Timeout — retorna o que tiver (parcial) ou None
    if resultados:
        logger.warning("⚠️  [SYNTH WORKER] Timeout parcial: %d/%d deps",
                       len(resultados), len(depends_on))
        return resultados
    return None


def _sintetizar_com_pro(
    chunks: list[dict],
    plan_ctx: dict,
    max_tokens: int,
) -> str:
    """Chama Gemini Pro de forma síncrona (Celery worker)."""
    import asyncio
    return asyncio.run(_sintetizar_async(chunks, plan_ctx, max_tokens))


async def _sintetizar_async(
    chunks: list[dict],
    plan_ctx: dict,
    max_tokens: int,
) -> str:
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types

    query       = plan_ctx.get("query", "")
    user_ctx    = plan_ctx.get("user_context", {})
    history     = plan_ctx.get("history", "")
    fatos       = plan_ctx.get("fatos", [])

    # Monta contexto RAG
    contexto_rag = ""
    for i, chunk in enumerate(chunks, 1):
        source  = chunk.get("source", "")
        content = chunk.get("content", "").strip()
        if content:
            contexto_rag += f"\n[{i}. {source}]\n{content}\n"

    # Monta prompt completo
    parts = []
    nome  = user_ctx.get("nome", "")
    curso = user_ctx.get("curso", "")
    if nome or curso:
        parts.append(f"<contexto_aluno>Aluno: {nome}" +
                     (f" | Curso: {curso}" if curso else "") + "</contexto_aluno>")
    if fatos:
        parts.append("<perfil>\n" + "\n".join(f"- {f}" for f in fatos[:3]) + "\n</perfil>")
    if history:
        parts.append(f"<historico>\n{history[-400:]}\n</historico>")

    parts.append(f"<contexto_rag>\n{contexto_rag or 'Nenhuma informação encontrada.'}\n</contexto_rag>")
    parts.append(f"<pergunta>{query}</pergunta>")

    prompt = "\n\n".join(parts)

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = await client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_SYNTHESIS,
            temperature=0.2,
            max_output_tokens=max_tokens,
        ),
    )

    usage = response.usage_metadata
    if usage:
        _PRO_TOKENS.labels(direction="input").inc(usage.prompt_token_count or 0)
        _PRO_TOKENS.labels(direction="output").inc(usage.candidates_token_count or 0)

    return (response.text or "").strip()


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