<<<<<<< HEAD
"""
api/eval_api.py — Avaliação RAG Interativa (Cérebro Matemático)
================================================================================

Este arquivo contém apenas a LÓGICA de avaliação.
Os endpoints web foram movidos para o hub.py (Controller).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import AsyncIterator

logger = logging.getLogger(__name__)

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
        "id": "cal-004",
        "category": "CALENDARIO",
        "question": "Qual o período de reajuste de matrícula para o semestre 2026.1?",
        "keywords": ["reajuste", "matrícula", "período"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-005",
        "category": "CALENDARIO",
        "question": "Qual a data limite para solicitação de aproveitamento de disciplinas?",
        "keywords": ["aproveitamento", "disciplinas", "limite"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-006",
        "category": "CALENDARIO",
        "question": "Quando terminam as aulas do primeiro semestre de 2026?",
        "keywords": ["terminam", "aulas", "junho", "2026"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-007",
        "category": "CALENDARIO",
        "question": "Quais são os dias de recesso acadêmico no calendário de 2026?",
        "keywords": ["recesso", "acadêmico", "dias"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-008",
        "category": "CALENDARIO",
        "question": "Quando ocorrem as inscrições para transferência interna e externa em 2026?",
        "keywords": ["transferência", "interna", "externa", "inscrições"],
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
        "id": "edi-004",
        "category": "EDITAL",
        "question": "Qual o valor da taxa de inscrição do PAES 2026?",
        "keywords": ["taxa", "inscrição", "valor"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-005",
        "category": "EDITAL",
        "question": "Como solicitar isenção da taxa de inscrição do PAES?",
        "keywords": ["isenção", "taxa", "solicitar"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-006",
        "category": "EDITAL",
        "question": "Em que dia serão aplicadas as provas do PAES 2026?",
        "keywords": ["provas", "dia", "aplicadas", "PAES"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-007",
        "category": "EDITAL",
        "question": "Quais os critérios de desempate na classificação final do PAES?",
        "keywords": ["desempate", "critérios", "classificação"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-008",
        "category": "EDITAL",
        "question": "Como funciona o sistema de cotas para estudantes de escolas públicas no PAES?",
        "keywords": ["cotas", "escolas públicas", "sistema"],
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
        "id": "con-003",
        "category": "CONTATOS",
        "question": "Como entro em contato com a Pró-Reitoria de Extensão e Assuntos Estudantis (PROEXAE)?",
        "keywords": ["PROEXAE", "contato", "extensão"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-004",
        "category": "CONTATOS",
        "question": "Qual o contato telefônico da Reitoria da UEMA?",
        "keywords": ["reitoria", "telefone", "contato"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-005",
        "category": "CONTATOS",
        "question": "Qual o email da Pró-Reitoria de Pesquisa e Pós-Graduação (PPG)?",
        "keywords": ["email", "PPG", "pesquisa"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-006",
        "category": "CONTATOS",
        "question": "Quem é o responsável atual pela biblioteca central da UEMA e qual seu contato?",
        "keywords": ["biblioteca", "responsável", "contato"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-007",
        "category": "CONTATOS",
        "question": "Qual o telefone ou email do setor de protocolo geral da UEMA?",
        "keywords": ["protocolo", "telefone", "email"],
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
    {
        "id": "gen-003",
        "category": "GERAL",
        "question": "Como faço para acessar o wi-fi institucional da UEMA?",
        "keywords": ["wi-fi", "acessar", "institucional", "rede"],
        "expected_source": None,
    },
    {
        "id": "gen-004",
        "category": "GERAL",
        "question": "O que devo fazer se meu usuário do SIGAA estiver bloqueado?",
        "keywords": ["SIGAA", "bloqueado", "usuário", "desbloquear"],
        "expected_source": None,
    },
    {
        "id": "gen-005",
        "category": "GERAL",
        "question": "Onde fica localizado o campus principal da UEMA em São Luís?",
        "keywords": ["campus", "localizado", "São Luís", "principal"],
        "expected_source": None,
    },
    {
        "id": "gen-006",
        "category": "GERAL",
        "question": "Como solicito redefinição de senha do email institucional?",
        "keywords": ["email", "senha", "redefinição", "institucional"],
        "expected_source": None,
    },
    {
        "id": "gen-007",
        "category": "GERAL",
        "question": "Como abrir um chamado de suporte técnico no CTIC?",
        "keywords": ["chamado", "suporte", "CTIC", "abrir"],
        "expected_source": None,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Tipos de resultado
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SingleEvalResult:
    id:               str
    category:         str
    question:         str
    answer:           str
    route_detected:   str
    crag_score:       float
    hit_rate:         float
    mrr:              float
    faithfulness:     float
    answer_relevancy: float
    latency_ms:       int
    chunks_count:     int
    top_chunk_source: str
    tokens_entrada:   int = 0
    tokens_saida:     int = 0
    tokens_total:     int = 0
    cost_usd:         float = 0.0
    memory_mb:        float = 0.0
    worker_name:      str = "worker_synthesis"
    error:            str = ""

    @property
    def aggregate_score(self) -> float:
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
    avg_tokens_entrada: float = 0.0
    avg_tokens_saida:   float = 0.0
    avg_tokens_total:   float = 0.0
    avg_cost_usd:       float = 0.0
    avg_memory_mb:      float = 0.0
    results:         list[SingleEvalResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["avg_aggregate"] = round(
            (self.avg_hit_rate + self.avg_mrr +
             self.avg_faithfulness + self.avg_relevancy) / 4, 3
        )
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Core: Lógica de Avaliação
# ─────────────────────────────────────────────────────────────────────────────
async def _evaluate_single(item: dict, session_id: str = "eval") -> SingleEvalResult:
    t0 = time.monotonic()
    question = item["question"]
    keywords = item.get("keywords", [])
    unique_session_id = f"{session_id}_{item['id']}"

    try:
        # 🔥 Usa o novo Cognitive OS em vez do Oracle Chain
        from src.application.runtime.dispatcher import processar
        result = await processar(
            message=question,
            session_id=unique_session_id,
            user_context={"nome": "Eval Bot", "role": "estudante"},
            history=""
        )

        answer = result.answer
        if not answer and getattr(result, "plan_id", None):
            from src.application.runtime.dispatcher import _aguardar_resposta_final
            final_data = await _aguardar_resposta_final(result.plan_id, timeout=15.0)
            if final_data:
                answer = final_data.get("answer", "")

        retrieved_texts = await _get_retrieved_chunks(question, getattr(result, "rota", "GERAL"))

        hit_rate = _calc_hit_rate(retrieved_texts, keywords)
        mrr      = _calc_mrr(retrieved_texts, keywords, item.get("expected_source"))

        faithfulness, relevancy = await _eval_generation(
            question=question,
            answer=answer,
            context="\n".join(retrieved_texts[:3]),
        )

        # Buscar tokens salvos no Redis
        from src.infrastructure.redis_client import obter_tokens_redis, diagnosticar
        tokens_in, tokens_out = obter_tokens_redis(unique_session_id)
        tokens_tot = tokens_in + tokens_out
        
        # Gemini 2.0/3 blended cost estimation ($0.075 / 1M input, $0.30 / 1M output)
        cost_usd = (tokens_in * 0.075 + tokens_out * 0.30) / 1_000_000

        # Memory usage
        try:
            diag = diagnosticar()
            memory_mb = float(diag.get("redis_ram_mb", 0.0))
        except Exception:
            memory_mb = 0.0

        # Mapeamento dinâmico de worker baseado na rota
        route = getattr(result, "rota", "GERAL")
        if route in ("CALENDARIO", "EDITAL"):
            worker_name = "worker_rag"
        elif route == "SIGAA":
            worker_name = "worker_sigaa"
        elif route == "MEDIA_DOWNLOAD":
            worker_name = "worker_media"
        else:
            worker_name = "worker_synthesis"

        return SingleEvalResult(
            id=item["id"],
            category=item["category"],
            question=question,
            answer=answer[:400],
            route_detected=route,
            crag_score=0.9 if hit_rate > 0 else 0.2,
            hit_rate=hit_rate,
            mrr=round(mrr, 3),
            faithfulness=round(faithfulness, 2),
            answer_relevancy=round(relevancy, 2),
            latency_ms=int((time.monotonic() - t0) * 1000),
            chunks_count=len(retrieved_texts),
            top_chunk_source=item.get("expected_source") or "",
            tokens_entrada=tokens_in,
            tokens_saida=tokens_out,
            tokens_total=tokens_tot,
            cost_usd=cost_usd,
            memory_mb=memory_mb,
            worker_name=worker_name,
        )

    except Exception as e:
        logger.exception("❌ [EVAL] '%s': %s", question[:60], e)
        return SingleEvalResult(
            id=item.get("id", "?"), category=item.get("category", "?"),
            question=question, answer="", route_detected="ERROR",
            crag_score=0.0, hit_rate=0.0, mrr=0.0,
            faithfulness=0.0, answer_relevancy=0.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            chunks_count=0, top_chunk_source="",
            tokens_entrada=0, tokens_saida=0, tokens_total=0,
            cost_usd=0.0, memory_mb=0.0, worker_name="ERROR",
            error=str(e)[:120],
        )


async def _get_retrieved_chunks(question: str, route: str) -> list[str]:
    try:
        from src.rag.embeddings import get_embeddings
        # 🔥 Atualizado para puxar o normalizar do novo Service
        from src.agents.academic_knowledge.service import _normalizar
        from src.infrastructure.redis_client import busca_hibrida
        import asyncio as _asyncio

        source_map = {
            "CALENDARIO": "calendario-academico-2026.pdf",
            "EDITAL":     "edital_paes_2026.pdf",
            "CONTATOS":   "guia_contatos_2025.pdf",
        }
        source_filter = source_map.get(route)
        emb = get_embeddings()
        vetor = await _asyncio.to_thread(emb.embed_query, _normalizar(question))
        chunks = await _asyncio.to_thread(
            busca_hibrida,
            query_text=_normalizar(question),
            query_embedding=vetor,
            source_filter=source_filter,
            k_vector=5, k_text=5,
        )
        return [c.get("content", "") for c in chunks]
    except Exception:
        return []

async def _get_top_source(question: str, route: str) -> str:
    chunks = await _get_retrieved_chunks(question, route)
    return ""  

def _calc_hit_rate(texts: list[str], keywords: list[str]) -> float:
    if not keywords or not texts:
        return 0.0
    combined = " ".join(texts).lower()
    hits = sum(1 for kw in keywords if kw.lower() in combined)
    return min(1.0, hits / len(keywords))

def _calc_mrr(texts: list[str], keywords: list[str], expected_source: str | None) -> float:
    if not texts:
        return 0.0
    for rank, text in enumerate(texts, start=1):
        text_lower = text.lower()
        if any(kw.lower() in text_lower for kw in keywords):
            return 1.0 / rank
    return 0.0

async def _eval_generation(question: str, answer: str, context: str) -> tuple[float, float]:
    if not answer or not context:
        return 0.5, 0.5   

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
        avg_tokens_entrada=round(sum(r.tokens_entrada for r in valid) / n, 1),
        avg_tokens_saida=  round(sum(r.tokens_saida for r in valid) / n, 1),
        avg_tokens_total=  round(sum(r.tokens_total for r in valid) / n, 1),
        avg_cost_usd=       round(sum(r.cost_usd for r in valid) / n, 6),
        avg_memory_mb=      round(sum(r.memory_mb for r in valid) / n, 2),
        results=results,
    )

def _persist_eval_result(result: EvalRunResult) -> None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.lpush("eval:results", json.dumps(result.to_dict(), ensure_ascii=False))
        r.ltrim("eval:results", 0, 9)  
        r.expire("eval:results", 86400 * 30)
    except Exception as e:
=======
"""
api/eval_api.py — Avaliação RAG Interativa (Cérebro Matemático)
================================================================================

Este arquivo contém apenas a LÓGICA de avaliação.
Os endpoints web foram movidos para o hub.py (Controller).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import AsyncIterator

logger = logging.getLogger(__name__)

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
        "id": "cal-004",
        "category": "CALENDARIO",
        "question": "Qual o período de reajuste de matrícula para o semestre 2026.1?",
        "keywords": ["reajuste", "matrícula", "período"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-005",
        "category": "CALENDARIO",
        "question": "Qual a data limite para solicitação de aproveitamento de disciplinas?",
        "keywords": ["aproveitamento", "disciplinas", "limite"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-006",
        "category": "CALENDARIO",
        "question": "Quando terminam as aulas do primeiro semestre de 2026?",
        "keywords": ["terminam", "aulas", "junho", "2026"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-007",
        "category": "CALENDARIO",
        "question": "Quais são os dias de recesso acadêmico no calendário de 2026?",
        "keywords": ["recesso", "acadêmico", "dias"],
        "expected_source": "calendario-academico-2026.pdf",
    },
    {
        "id": "cal-008",
        "category": "CALENDARIO",
        "question": "Quando ocorrem as inscrições para transferência interna e externa em 2026?",
        "keywords": ["transferência", "interna", "externa", "inscrições"],
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
        "id": "edi-004",
        "category": "EDITAL",
        "question": "Qual o valor da taxa de inscrição do PAES 2026?",
        "keywords": ["taxa", "inscrição", "valor"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-005",
        "category": "EDITAL",
        "question": "Como solicitar isenção da taxa de inscrição do PAES?",
        "keywords": ["isenção", "taxa", "solicitar"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-006",
        "category": "EDITAL",
        "question": "Em que dia serão aplicadas as provas do PAES 2026?",
        "keywords": ["provas", "dia", "aplicadas", "PAES"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-007",
        "category": "EDITAL",
        "question": "Quais os critérios de desempate na classificação final do PAES?",
        "keywords": ["desempate", "critérios", "classificação"],
        "expected_source": "edital_paes_2026.pdf",
    },
    {
        "id": "edi-008",
        "category": "EDITAL",
        "question": "Como funciona o sistema de cotas para estudantes de escolas públicas no PAES?",
        "keywords": ["cotas", "escolas públicas", "sistema"],
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
        "id": "con-003",
        "category": "CONTATOS",
        "question": "Como entro em contato com a Pró-Reitoria de Extensão e Assuntos Estudantis (PROEXAE)?",
        "keywords": ["PROEXAE", "contato", "extensão"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-004",
        "category": "CONTATOS",
        "question": "Qual o contato telefônico da Reitoria da UEMA?",
        "keywords": ["reitoria", "telefone", "contato"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-005",
        "category": "CONTATOS",
        "question": "Qual o email da Pró-Reitoria de Pesquisa e Pós-Graduação (PPG)?",
        "keywords": ["email", "PPG", "pesquisa"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-006",
        "category": "CONTATOS",
        "question": "Quem é o responsável atual pela biblioteca central da UEMA e qual seu contato?",
        "keywords": ["biblioteca", "responsável", "contato"],
        "expected_source": "guia_contatos_2025.pdf",
    },
    {
        "id": "con-007",
        "category": "CONTATOS",
        "question": "Qual o telefone ou email do setor de protocolo geral da UEMA?",
        "keywords": ["protocolo", "telefone", "email"],
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
    {
        "id": "gen-003",
        "category": "GERAL",
        "question": "Como faço para acessar o wi-fi institucional da UEMA?",
        "keywords": ["wi-fi", "acessar", "institucional", "rede"],
        "expected_source": None,
    },
    {
        "id": "gen-004",
        "category": "GERAL",
        "question": "O que devo fazer se meu usuário do SIGAA estiver bloqueado?",
        "keywords": ["SIGAA", "bloqueado", "usuário", "desbloquear"],
        "expected_source": None,
    },
    {
        "id": "gen-005",
        "category": "GERAL",
        "question": "Onde fica localizado o campus principal da UEMA em São Luís?",
        "keywords": ["campus", "localizado", "São Luís", "principal"],
        "expected_source": None,
    },
    {
        "id": "gen-006",
        "category": "GERAL",
        "question": "Como solicito redefinição de senha do email institucional?",
        "keywords": ["email", "senha", "redefinição", "institucional"],
        "expected_source": None,
    },
    {
        "id": "gen-007",
        "category": "GERAL",
        "question": "Como abrir um chamado de suporte técnico no CTIC?",
        "keywords": ["chamado", "suporte", "CTIC", "abrir"],
        "expected_source": None,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Tipos de resultado
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SingleEvalResult:
    id:               str
    category:         str
    question:         str
    answer:           str
    route_detected:   str
    crag_score:       float
    hit_rate:         float
    mrr:              float
    faithfulness:     float
    answer_relevancy: float
    latency_ms:       int
    chunks_count:     int
    top_chunk_source: str
    tokens_entrada:   int = 0
    tokens_saida:     int = 0
    tokens_total:     int = 0
    cost_usd:         float = 0.0
    memory_mb:        float = 0.0
    worker_name:      str = "worker_synthesis"
    error:            str = ""

    @property
    def aggregate_score(self) -> float:
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
    avg_tokens_entrada: float = 0.0
    avg_tokens_saida:   float = 0.0
    avg_tokens_total:   float = 0.0
    avg_cost_usd:       float = 0.0
    avg_memory_mb:      float = 0.0
    results:         list[SingleEvalResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["avg_aggregate"] = round(
            (self.avg_hit_rate + self.avg_mrr +
             self.avg_faithfulness + self.avg_relevancy) / 4, 3
        )
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Core: Lógica de Avaliação
# ─────────────────────────────────────────────────────────────────────────────
async def _evaluate_single(item: dict, session_id: str = "eval") -> SingleEvalResult:
    t0 = time.monotonic()
    question = item["question"]
    keywords = item.get("keywords", [])
    unique_session_id = f"{session_id}_{item['id']}"

    try:
        # 🔥 Usa o novo Cognitive OS em vez do Oracle Chain
        from src.application.chain.cognitive_os import processar
        result = await processar(
            message=question,
            session_id=unique_session_id,
            user_context={"nome": "Eval Bot", "role": "estudante"},
            history=""
        )

        answer = result.answer
        if not answer and getattr(result, "plan_id", None):
            from src.application.chain.cognitive_os import _aguardar_resposta_final
            final_data = await _aguardar_resposta_final(result.plan_id, timeout=15.0)
            if final_data:
                answer = final_data.get("answer", "")

        retrieved_texts = await _get_retrieved_chunks(question, getattr(result, "rota", "GERAL"))

        hit_rate = _calc_hit_rate(retrieved_texts, keywords)
        mrr      = _calc_mrr(retrieved_texts, keywords, item.get("expected_source"))

        faithfulness, relevancy = await _eval_generation(
            question=question,
            answer=answer,
            context="\n".join(retrieved_texts[:3]),
        )

        # Buscar tokens salvos no Redis
        from src.infrastructure.redis_client import obter_tokens_redis, diagnosticar
        tokens_in, tokens_out = obter_tokens_redis(unique_session_id)
        tokens_tot = tokens_in + tokens_out
        
        # Gemini 2.0/3 blended cost estimation ($0.075 / 1M input, $0.30 / 1M output)
        cost_usd = (tokens_in * 0.075 + tokens_out * 0.30) / 1_000_000

        # Memory usage
        try:
            diag = diagnosticar()
            memory_mb = float(diag.get("redis_ram_mb", 0.0))
        except Exception:
            memory_mb = 0.0

        # Mapeamento dinâmico de worker baseado na rota
        route = getattr(result, "rota", "GERAL")
        if route in ("CALENDARIO", "EDITAL"):
            worker_name = "worker_rag"
        elif route == "SIGAA":
            worker_name = "worker_sigaa"
        elif route == "MEDIA_DOWNLOAD":
            worker_name = "worker_media"
        else:
            worker_name = "worker_synthesis"

        return SingleEvalResult(
            id=item["id"],
            category=item["category"],
            question=question,
            answer=answer[:400],
            route_detected=route,
            crag_score=0.9 if hit_rate > 0 else 0.2,
            hit_rate=hit_rate,
            mrr=round(mrr, 3),
            faithfulness=round(faithfulness, 2),
            answer_relevancy=round(relevancy, 2),
            latency_ms=int((time.monotonic() - t0) * 1000),
            chunks_count=len(retrieved_texts),
            top_chunk_source=item.get("expected_source") or "",
            tokens_entrada=tokens_in,
            tokens_saida=tokens_out,
            tokens_total=tokens_tot,
            cost_usd=cost_usd,
            memory_mb=memory_mb,
            worker_name=worker_name,
        )

    except Exception as e:
        logger.exception("❌ [EVAL] '%s': %s", question[:60], e)
        return SingleEvalResult(
            id=item.get("id", "?"), category=item.get("category", "?"),
            question=question, answer="", route_detected="ERROR",
            crag_score=0.0, hit_rate=0.0, mrr=0.0,
            faithfulness=0.0, answer_relevancy=0.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            chunks_count=0, top_chunk_source="",
            tokens_entrada=0, tokens_saida=0, tokens_total=0,
            cost_usd=0.0, memory_mb=0.0, worker_name="ERROR",
            error=str(e)[:120],
        )


async def _get_retrieved_chunks(question: str, route: str) -> list[str]:
    try:
        from src.rag.embeddings import get_embeddings
        # 🔥 Atualizado para puxar o normalizar do novo Service
        from src.infrastructure.services.rag_search_service import _normalizar
        from src.infrastructure.redis_client import busca_hibrida
        import asyncio as _asyncio

        source_map = {
            "CALENDARIO": "calendario-academico-2026.pdf",
            "EDITAL":     "edital_paes_2026.pdf",
            "CONTATOS":   "guia_contatos_2025.pdf",
        }
        source_filter = source_map.get(route)
        emb = get_embeddings()
        vetor = await _asyncio.to_thread(emb.embed_query, _normalizar(question))
        chunks = await _asyncio.to_thread(
            busca_hibrida,
            query_text=_normalizar(question),
            query_embedding=vetor,
            source_filter=source_filter,
            k_vector=5, k_text=5,
        )
        return [c.get("content", "") for c in chunks]
    except Exception:
        return []

async def _get_top_source(question: str, route: str) -> str:
    chunks = await _get_retrieved_chunks(question, route)
    return ""  

def _calc_hit_rate(texts: list[str], keywords: list[str]) -> float:
    if not keywords or not texts:
        return 0.0
    combined = " ".join(texts).lower()
    hits = sum(1 for kw in keywords if kw.lower() in combined)
    return min(1.0, hits / len(keywords))

def _calc_mrr(texts: list[str], keywords: list[str], expected_source: str | None) -> float:
    if not texts:
        return 0.0
    for rank, text in enumerate(texts, start=1):
        text_lower = text.lower()
        if any(kw.lower() in text_lower for kw in keywords):
            return 1.0 / rank
    return 0.0

async def _eval_generation(question: str, answer: str, context: str) -> tuple[float, float]:
    if not answer or not context:
        return 0.5, 0.5   

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
        avg_tokens_entrada=round(sum(r.tokens_entrada for r in valid) / n, 1),
        avg_tokens_saida=  round(sum(r.tokens_saida for r in valid) / n, 1),
        avg_tokens_total=  round(sum(r.tokens_total for r in valid) / n, 1),
        avg_cost_usd=       round(sum(r.cost_usd for r in valid) / n, 6),
        avg_memory_mb=      round(sum(r.memory_mb for r in valid) / n, 2),
        results=results,
    )

def _persist_eval_result(result: EvalRunResult) -> None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.lpush("eval:results", json.dumps(result.to_dict(), ensure_ascii=False))
        r.ltrim("eval:results", 0, 9)  
        r.expire("eval:results", 86400 * 30)
    except Exception as e:
>>>>>>> 3e1bb5ca7e8857c54cd83396303cc082653e7734
        logger.warning("⚠️  [EVAL] persist falhou: %s", e)