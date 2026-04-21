"""
api/eval_api.py — Avaliação RAG Interativa (baseado no athina-ai/rag-cookbooks)
================================================================================

MÉTRICAS IMPLEMENTADAS (sem dependências externas pagas):
  1. Hit Rate        → o chunk correto foi recuperado? (presença de keyword)
  2. MRR             → Mean Reciprocal Rank — quão cedo aparece o chunk correto?
  3. CRAG Score      → qualidade do retrieval (rrf_score do top chunk)
  4. Faithfulness    → a resposta usa APENAS o contexto? (LLM como juiz)
  5. Answer Relevancy → a resposta responde a pergunta? (LLM como juiz)

ENDPOINTS:
  GET  /eval/          → página HTML (servida pelo hub)
  GET  /eval/dataset   → dataset de teste embutido (UEMA específico)
  POST /eval/run       → executa avaliação em um conjunto de perguntas
  GET  /eval/results   → últimos resultados armazenados no Redis
  POST /eval/single    → avalia UMA pergunta (para o botão "Testar" do UI)
  GET  /eval/stream    → SSE para acompanhar progresso em tempo real

COOKBOOK REFERÊNCIA:
  github.com/athina-ai/rag-cookbooks
  Implementamos: Naive RAG eval + CRAG + Retrieval metrics
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Dataset de teste embutido — perguntas UEMA com ground truth
# ─────────────────────────────────────────────────────────────────────────────

EVAL_DATASET = [
    {
        "id": "cal-001",
        "category": "CALENDARIO",
        "question": "Quando é a matrícula de veteranos no semestre 2026.1?",
        "keywords": ["matrícula", "veteranos", "fevereiro", "2026"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-002",
        "category": "CALENDARIO",
        "question": "Qual é a data de início das aulas em 2026?",
        "keywords": ["início", "aulas", "fevereiro", "2026"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-003",
        "category": "CALENDARIO",
        "question": "Quando é o prazo para trancamento de matrícula?",
        "keywords": ["trancamento", "prazo"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "edi-001",
        "category": "EDITAL",
        "question": "Quantas vagas tem o curso de Engenharia Civil no PAES 2026?",
        "keywords": ["engenharia civil", "vagas", "PAES"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-002",
        "category": "EDITAL",
        "question": "O que é a categoria BR-PPI no PAES?",
        "keywords": ["BR-PPI", "pretos", "pardos", "indígenas"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-003",
        "category": "EDITAL",
        "question": "Quais documentos preciso para me inscrever no PAES 2026?",
        "keywords": ["documentos", "inscrição"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "con-001",
        "category": "CONTATOS",
        "question": "Qual o email da Pró-Reitoria de Graduação (PROG)?",
        "keywords": ["email", "PROG", "graduação"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-002",
        "category": "CONTATOS",
        "question": "Qual o telefone do CTIC para suporte técnico?",
        "keywords": ["CTIC", "telefone", "suporte"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "gen-001",
        "category": "GERAL",
        "question": "O que é a UEMA?",
        "keywords": ["universidade", "maranhão", "UEMA"],
        "expected_source": None,
    },
    {
        "id": "gen-002",
        "category": "GERAL",
        "question": "Quais são os centros acadêmicos da UEMA?",
        "keywords": ["CECEN", "CESB", "centro"],
        "expected_source": None,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de resultado
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SingleEvalResult:
    """Resultado da avaliação de uma pergunta."""
    id:               str
    category:         str
    question:         str
    answer:           str
    route_detected:   str
    crag_score:       float
    hit_rate:         float    # 0 ou 1 — foi encontrado keyword no contexto?
    mrr:              float    # 1/rank do primeiro chunk relevante
    faithfulness:     float    # 0.0-1.0 (LLM como juiz)
    answer_relevancy: float    # 0.0-1.0 (LLM como juiz)
    latency_ms:       int
    chunks_count:     int
    top_chunk_source: str
    error:            str = ""

    @property
    def aggregate_score(self) -> float:
        """Score agregado simples: média ponderada das métricas."""
        if self.error:
            return 0.0
        return (
            self.hit_rate * 0.25 +
            self.mrr * 0.25 +
            self.faithfulness * 0.25 +
            self.answer_relevancy * 0.25
        )


@dataclass
class EvalRunResult:
    """Resultado de uma rodada completa de avaliação."""
    run_id:          str
    timestamp:       str
    total_questions: int
    completed:       int
    avg_hit_rate:    float = 0.0
    avg_mrr:         float = 0.0
    avg_crag:        float = 0.0
    avg_faithfulness: float = 0.0
    avg_relevancy:   float = 0.0
    avg_latency_ms:  int = 0
    results:         list[SingleEvalResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["avg_aggregate"] = round(
            (self.avg_hit_rate + self.avg_mrr +
             self.avg_faithfulness + self.avg_relevancy) / 4, 3
        )
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Core: avalia uma única pergunta
# ─────────────────────────────────────────────────────────────────────────────

async def _evaluate_single(item: dict, session_id: str = "eval") -> SingleEvalResult:
    """
    Avalia uma pergunta do dataset com todas as métricas.
    Segue o padrão do cookbook: retrieval metrics + generation metrics.
    """
    t0 = time.monotonic()
    question = item["question"]
    keywords = item.get("keywords", [])
    expected_source = item.get("expected_source")

    try:
        from src.application.chain.oracle_chain import get_oracle_chain
        chain = get_oracle_chain()
        result = await chain.invoke(
            message=question,
            session_id=session_id,
            user_context={"nome": "Eval Bot", "role": "estudante"},
        )

        # ── Retrieval Metrics ────────────────────────────────────────────────

        # Hit Rate: algum keyword da ground truth aparece nos chunks?
        chunks = result.steps   # pegamos dos steps o detalhe
        # Melhor: busca nos dados do step "retrieve"
        from src.infrastructure.redis_client import get_redis_text
        # Reconstruímos a busca para ter os chunks completos
        retrieved_texts = await _get_retrieved_chunks(question, result.route)

        hit_rate = _calc_hit_rate(retrieved_texts, keywords)
        mrr      = _calc_mrr(retrieved_texts, keywords, expected_source)

        # ── Generation Metrics ────────────────────────────────────────────────

        faithfulness, relevancy = await _eval_generation(
            question=question,
            answer=result.answer,
            context="\n".join(retrieved_texts[:3]),
        )

        latency = int((time.monotonic() - t0) * 1000)
        top_source = ""
        if retrieved_texts:
            # Obtém source do top chunk
            top_source = await _get_top_source(question, result.route)

        return SingleEvalResult(
            id=item["id"],
            category=item["category"],
            question=question,
            answer=result.answer[:400],
            route_detected=result.route,
            crag_score=round(result.crag_score, 3),
            hit_rate=hit_rate,
            mrr=round(mrr, 3),
            faithfulness=round(faithfulness, 2),
            answer_relevancy=round(relevancy, 2),
            latency_ms=latency,
            chunks_count=result.chunks_count,
            top_chunk_source=top_source,
        )

    except Exception as e:
        logger.exception("❌ [EVAL] Falha ao avaliar '%s': %s", question[:60], e)
        return SingleEvalResult(
            id=item.get("id", "?"),
            category=item.get("category", "?"),
            question=question,
            answer="",
            route_detected="ERROR",
            crag_score=0.0,
            hit_rate=0.0,
            mrr=0.0,
            faithfulness=0.0,
            answer_relevancy=0.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            chunks_count=0,
            top_chunk_source="",
            error=str(e)[:120],
        )


async def _get_retrieved_chunks(question: str, route: str) -> list[str]:
    """Obtém textos dos chunks recuperados para calcular métricas de retrieval."""
    try:
        from src.rag.embeddings import get_embeddings
        from src.application.chain.oracle_chain import _normalize
        from src.infrastructure.redis_client import busca_hibrida
        import asyncio as _asyncio

        source_map = {
            "CALENDARIO": "calendario-academico-2026.pdf",
            "EDITAL":     "edital_paes_2026.pdf",
            "CONTATOS":   "guia_contatos_2025.pdf",
        }
        source_filter = source_map.get(route)
        emb = get_embeddings()
        vetor = await _asyncio.to_thread(emb.embed_query, _normalize(question))
        chunks = await _asyncio.to_thread(
            busca_hibrida,
            query_text=_normalize(question),
            query_embedding=vetor,
            source_filter=source_filter,
            k_vector=5, k_text=5,
        )
        return [c.get("content", "") for c in chunks]
    except Exception:
        return []


async def _get_top_source(question: str, route: str) -> str:
    chunks = await _get_retrieved_chunks(question, route)
    return ""  # simplificado


def _calc_hit_rate(texts: list[str], keywords: list[str]) -> float:
    """Hit Rate: pelo menos 1 keyword aparece nos textos recuperados?"""
    if not keywords or not texts:
        return 0.0
    combined = " ".join(texts).lower()
    hits = sum(1 for kw in keywords if kw.lower() in combined)
    return min(1.0, hits / len(keywords))


def _calc_mrr(texts: list[str], keywords: list[str],
              expected_source: str | None) -> float:
    """MRR: 1/posição do primeiro chunk relevante."""
    if not texts:
        return 0.0
    for rank, text in enumerate(texts, start=1):
        text_lower = text.lower()
        if any(kw.lower() in text_lower for kw in keywords):
            return 1.0 / rank
    return 0.0


async def _eval_generation(question: str, answer: str, context: str) -> tuple[float, float]:
    """
    Avalia faithfulness e answer relevancy usando Gemini como juiz.
    Retorna (faithfulness, answer_relevancy) entre 0.0 e 1.0.
    """
    if not answer or not context:
        return 0.5, 0.5   # sem contexto para julgar

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage
        from src.infrastructure.settings import settings

        llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            temperature=0.0,
            google_api_key=settings.GEMINI_API_KEY,
        )

        prompt_faithfulness = f"""Avalie se a RESPOSTA usa APENAS informações do CONTEXTO.

CONTEXTO:
{context[:600]}

RESPOSTA:
{answer[:400]}

Responda APENAS com um número de 0.0 a 1.0 onde:
1.0 = resposta completamente fiel ao contexto
0.5 = parcialmente fiel
0.0 = resposta inventa informações não presentes no contexto"""

        prompt_relevancy = f"""Avalie se a RESPOSTA é relevante para a PERGUNTA.

PERGUNTA: {question}
RESPOSTA: {answer[:400]}

Responda APENAS com um número de 0.0 a 1.0 onde:
1.0 = resposta completamente relevante e responde a pergunta
0.5 = parcialmente relevante
0.0 = não responde a pergunta"""

        # Executa ambas em paralelo
        faith_resp, relev_resp = await asyncio.gather(
            llm.ainvoke([HumanMessage(content=prompt_faithfulness)]),
            llm.ainvoke([HumanMessage(content=prompt_relevancy)]),
        )

        def parse_score(text: str) -> float:
            import re
            m = re.search(r"[01]\.?\d*", text.strip())
            if m:
                return max(0.0, min(1.0, float(m.group())))
            return 0.5

        faithfulness = parse_score(faith_resp.content)
        relevancy    = parse_score(relev_resp.content)
        return faithfulness, relevancy

    except Exception as e:
        logger.warning("⚠️  [EVAL] LLM judge falhou: %s", e)
        return 0.5, 0.5


def _aggregate_results(results: list[SingleEvalResult]) -> EvalRunResult:
    """Calcula médias de todas as métricas."""
    import uuid
    n = len([r for r in results if not r.error])
    if n == 0:
        return EvalRunResult(
            run_id=str(uuid.uuid4())[:8],
            timestamp=datetime.now().isoformat(),
            total_questions=len(results),
            completed=0,
        )

    valid = [r for r in results if not r.error]
    return EvalRunResult(
        run_id=str(uuid.uuid4())[:8],
        timestamp=datetime.now().isoformat(),
        total_questions=len(results),
        completed=n,
        avg_hit_rate=   round(sum(r.hit_rate for r in valid) / n, 3),
        avg_mrr=        round(sum(r.mrr for r in valid) / n, 3),
        avg_crag=       round(sum(r.crag_score for r in valid) / n, 3),
        avg_faithfulness=round(sum(r.faithfulness for r in valid) / n, 3),
        avg_relevancy=  round(sum(r.answer_relevancy for r in valid) / n, 3),
        avg_latency_ms= int(sum(r.latency_ms for r in valid) / n),
        results=results,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dataset")
async def get_dataset():
    """Retorna o dataset de avaliação."""
    return JSONResponse({"dataset": EVAL_DATASET, "total": len(EVAL_DATASET)})


@router.post("/single")
async def eval_single(request: Request):
    """Avalia uma única pergunta. Rápido para o botão 'Testar'."""
    try:
        body = await request.json()
        question = body.get("question", "").strip()
        if not question:
            return JSONResponse({"error": "question obrigatório"}, status_code=400)

        # Cria item sintético
        item = {
            "id":       "custom",
            "category": "CUSTOM",
            "question": question,
            "keywords": question.split()[:5],
            "expected_source": None,
        }
        result = await _evaluate_single(item, session_id="eval_single")
        return JSONResponse(asdict(result))

    except Exception as e:
        logger.exception("❌ [EVAL] /single falhou: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# Fila global de progresso para SSE
_eval_progress_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
_eval_running = False


@router.post("/run")
async def eval_run(request: Request):
    """
    Inicia avaliação completa em background.
    Progresso disponível via GET /eval/stream (SSE).
    """
    global _eval_running
    if _eval_running:
        return JSONResponse({"error": "Avaliação já em andamento"}, status_code=409)

    try:
        body = await request.json()
        ids = body.get("ids", None)   # None = todos
    except Exception:
        ids = None

    dataset = EVAL_DATASET
    if ids:
        dataset = [d for d in EVAL_DATASET if d["id"] in ids]

    # Executa em background task
    asyncio.create_task(_run_eval_background(dataset))

    return JSONResponse({
        "ok":    True,
        "total": len(dataset),
        "msg":   "Avaliação iniciada. Acompanhe em /eval/stream"
    })


async def _run_eval_background(dataset: list[dict]) -> None:
    global _eval_running
    _eval_running = True
    results = []

    await _eval_progress_queue.put(json.dumps({
        "type": "start", "total": len(dataset)
    }))

    for i, item in enumerate(dataset):
        await _eval_progress_queue.put(json.dumps({
            "type":     "progress",
            "current":  i + 1,
            "total":    len(dataset),
            "question": item["question"][:60],
        }))

        result = await _evaluate_single(item)
        results.append(result)

        await _eval_progress_queue.put(json.dumps({
            "type":       "result",
            "id":         result.id,
            "question":   result.question[:60],
            "hit_rate":   result.hit_rate,
            "mrr":        result.mrr,
            "crag":       result.crag_score,
            "faithfulness": result.faithfulness,
            "relevancy":  result.answer_relevancy,
            "latency_ms": result.latency_ms,
            "error":      result.error,
        }))

        # Pequena pausa entre perguntas para não saturar a API
        await asyncio.sleep(0.5)

    # Calcula e salva agregado
    run_result = _aggregate_results(results)
    _persist_eval_result(run_result)

    await _eval_progress_queue.put(json.dumps({
        "type":       "done",
        "run_id":     run_result.run_id,
        "avg_hit":    run_result.avg_hit_rate,
        "avg_mrr":    run_result.avg_mrr,
        "avg_crag":   run_result.avg_crag,
        "avg_faith":  run_result.avg_faithfulness,
        "avg_relev":  run_result.avg_relevancy,
        "avg_lat_ms": run_result.avg_latency_ms,
    }))

    _eval_running = False


@router.get("/stream")
async def eval_stream(request: Request):
    """SSE: progresso da avaliação em tempo real."""
    async def generator() -> AsyncIterator[str]:
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(_eval_progress_queue.get(), timeout=15.0)
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/results")
async def eval_results():
    """Retorna os últimos resultados de avaliação."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        raw = r.lrange("eval:results", 0, 4)
        results = [json.loads(item) for item in raw]
        return JSONResponse({"results": results})
    except Exception as e:
        return JSONResponse({"results": [], "error": str(e)})


def _persist_eval_result(result: EvalRunResult) -> None:
    """Persiste resultado no Redis."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.lpush("eval:results", json.dumps(result.to_dict(), ensure_ascii=False))
        r.ltrim("eval:results", 0, 9)  # guarda últimos 10
        r.expire("eval:results", 86400 * 30)
    except Exception as e:
        logger.warning("⚠️  [EVAL] persist falhou: %s", e)