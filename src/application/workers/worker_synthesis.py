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

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_SYNTHESIS = """<system_instruction>
Você é o Oráculo, o assistente virtual oficial da UEMA (Universidade Estadual do Maranhão) via WhatsApp.
Sua responsabilidade é responder à pergunta do usuário baseando-se estritamente nas informações oficiais fornecidas no bloco <contexto_rag> ou no <contexto_tarefa_anterior>.

<regras_de_grounding>
1. Grounding Estrito: Responda apenas com informações contidas no <contexto_rag> ou no <contexto_tarefa_anterior>.
2. Validação de Memória Contínua: Antes de dizer que não encontrou informações, valide se o <contexto_tarefa_anterior> responde à pergunta ou mantém o sentido da conversa. A conversa é fluida, e o usuário pode estar apenas reagindo a uma informação já enviada.
3. Tratamento de Falha: Se a resposta factual para a pergunta do usuário NÃO estiver explicitada no <contexto_rag> NEM no <contexto_tarefa_anterior>, responda exatamente e apenas: "Não encontrei essa informação nos meus registros. Consulte o site oficial em uema.br."
4. Proibição de Alucinações: NUNCA crie ou deduza datas, e-mails, telefones ou prazos que não estejam escritos nos documentos. Se faltar algum dado, use a recusa padrão.
</regras_de_grounding>

<instrucoes_de_capabilities>
- Se o usuário perguntar sobre suas capacidades (o que você faz, quem é você) e essa informação não estiver no RAG, você está AUTORIZADO a explicar suas principais funções (esclarecer dúvidas sobre o Calendário Acadêmico 2026, Edital PAES 2026, Contatos oficiais e suporte do CTIC) em um tom amigável, sem aplicar a recusa padrão.
</instrucoes_de_capabilities>

<formatacao_whatsapp>
- Limitação: Escreva de 1 a 3 parágrafos, de forma direta e concisa.
- Estilo: Utilize *negrito* para destacar datas importantes, e-mails, telefones, siglas de departamentos ou conceitos cruciais.
- Evite saudações repetitivas no início das respostas factuais.
</formatacao_whatsapp>
</system_instruction>"""

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

    # ── Gera resposta com Gemini Pro ───────────────────────────────────────────
    try:
        resposta = await _sintetizar_async(
            chunks=chunks_unicos[:6],
            plan_ctx=plan_ctx,
            max_tokens=max_tokens,
        )
        status = "ok"
    except Exception as exc:
        import google.genai.errors as genai_errors
        is_api_err = isinstance(exc, genai_errors.APIError)
        logger.exception("❌ [SYNTH WORKER] Pro falhou (API Error: %s): %s", is_api_err, exc)
        resposta = "Estou enfrentando lentidão, mas anotei sua dúvida. Tente novamente em alguns instantes. 🙏"
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

    from datetime import datetime

    # Injeta data/hora atual
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    parts = [f"<datetime>{now_str}</datetime>"]

    # Injeta histórico de conversa (Layer 1)
    if history:
        parts.append(f"<historico_conversa>\n{history[-1500:]}\n</historico_conversa>")

    # Injeta contexto da última tarefa (Layer 3)
    task_ctx = plan_ctx.get("task_history", {})
    if task_ctx.get("last_worker"):
        parts.append(
            f"<contexto_tarefa_anterior>\n"
            f"Worker: {task_ctx['last_worker']}\n"
            f"Resultado: {task_ctx.get('last_result', '')[:300]}\n"
            f"</contexto_tarefa_anterior>"
        )

    # Aluno
    nome  = user_ctx.get("nome", "")
    curso = user_ctx.get("curso", "")
    if nome or curso:
        parts.append(f"<contexto_aluno>Aluno: {nome}"
                     + (f" | Curso: {curso}" if curso else "") + "</contexto_aluno>")

    if fatos:
        parts.append("<perfil>\n" + "\n".join(f"- {f}" for f in fatos[:3]) + "\n</perfil>")

    # RAG
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
        session_id = plan_ctx.get("session_id")
        if session_id:
            from src.infrastructure.redis_client import registrar_tokens_redis
            registrar_tokens_redis(session_id, usage.prompt_token_count or 0, usage.candidates_token_count or 0)

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