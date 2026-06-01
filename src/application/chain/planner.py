"""
src/application/chain/planner.py
==================================
Planner Agent — Gemini Pro lê a intenção e gera um DAG de tarefas.

O DAG é um JSON que o Celery executa de forma desacoplada via Redis Streams.

EXEMPLO DE DAG GERADO:
{
  "plan_id": "uuid4",
  "session_id": "55989999...",
  "rota": "EDITAL",
  "steps": [
    {"id": "s1", "worker": "rag_search", "args": {"doc_type": "edital", "k": 10}, "depends_on": []},
    {"id": "s2", "worker": "synthesis",  "args": {"max_tokens": 512},              "depends_on": ["s1"]}
  ],
  "context": {
    "query": "...",
    "user_context": {...},
    "history": "..."
  }
}

MÉTRICAS:
  oraculo_planner_gemini_pro_tokens_total{direction}
  oraculo_planner_latency_ms
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# ── Métricas ──────────────────────────────────────────────────────────────────
_PRO_TOKENS = Counter(
    "oraculo_planner_gemini_pro_tokens_total",
    "Tokens do Gemini Pro no Planner",
    ["direction"],
)
_PLANNER_LATENCY = Histogram(
    "oraculo_planner_latency_ms",
    "Latência do Planner em ms",
    buckets=[50, 100, 250, 500, 1000, 2000],
)

# ── Prompt do Planner ─────────────────────────────────────────────────────────
_SYSTEM_PLANNER = """Você é o Planner do Oráculo UEMA. Sua única função é decompor uma tarefa em um plano de execução estruturado (DAG).

WORKERS DISPONÍVEIS:
- "rag_search": busca híbrida no Redis Vector Store. Args: {doc_type: str, k: int, query_override: str?}
- "synthesis": gera resposta final com Gemini Pro. Args: {max_tokens: int, tone: str?}
- "crud_confirm": solicita confirmação HITL antes de executar CRUD. Args: {action: str, description: str}
- "greeting": responde saudações sem RAG. Args: {}

REGRAS:
1. "synthesis" SEMPRE depende de "rag_search" (quando rag_search estiver no plano).
2. Não inventar workers. Use apenas os listados.
3. Para GREETING: apenas step "greeting", sem synthesis.
4. Para CRUD: apenas step "crud_confirm".
5. Para GERAL com dúvida complexa: considere 2 steps de rag_search paralelos com doc_types diferentes.

Responda APENAS com JSON válido (sem markdown, sem comentários):
{
  "steps": [
    {"id": "s1", "worker": "nome_worker", "args": {...}, "depends_on": []}
  ]
}"""


@dataclass
class ExecutionPlan:
    plan_id: str
    session_id: str
    rota: str
    steps: list[dict]
    context: dict
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "rota": self.rota,
            "steps": self.steps,
            "context": self.context,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionPlan":
        return cls(
            plan_id=d["plan_id"],
            session_id=d["session_id"],
            rota=d["rota"],
            steps=d["steps"],
            context=d["context"],
            created_at=d.get("created_at", time.time()),
        )


async def criar_plano(
    query: str,
    session_id: str,
    rota: str,
    dag_hint: dict,
    user_context: dict,
    history: str = "",
    fatos: list[str] | None = None,
) -> ExecutionPlan:
    """
    Cria um ExecutionPlan via Gemini Pro.
    Se o Pro falhar, cria um plano padrão baseado no dag_hint.
    """
    t0 = time.monotonic()

    # ── Fast-path: rotas simples não precisam do Pro ──────────────────────────
    if rota in ("GREETING",):
        plan = _plano_simples(rota, query, session_id, user_context, history, fatos or [])
        ms = int((time.monotonic() - t0) * 1000)
        _PLANNER_LATENCY.observe(ms)
        return plan

    if rota == "CRUD":
        plan = _plano_crud(query, session_id, user_context, history, fatos or [])
        ms = int((time.monotonic() - t0) * 1000)
        _PLANNER_LATENCY.observe(ms)
        return plan

    if rota == "MEDIA_DOWNLOAD":
        plan = _plano_media(query, session_id, user_context, history, fatos or [], dag_hint)
        ms = int((time.monotonic() - t0) * 1000)
        _PLANNER_LATENCY.observe(ms)
        return plan

    # ── Gemini Pro para planos RAG ────────────────────────────────────────────
    try:
        plan = await _planejar_com_pro(
            query, session_id, rota, dag_hint, user_context, history, fatos or []
        )
    except Exception as e:
        logger.error("❌ [PLANNER] Pro falhou, usando plano padrão: %s", e)
        plan = _plano_padrao_rag(rota, dag_hint, query, session_id, user_context, history, fatos or [])

    ms = int((time.monotonic() - t0) * 1000)
    _PLANNER_LATENCY.observe(ms)
    logger.info("📋 [PLANNER] Plano criado | %d steps | %dms | session=%s",
                len(plan.steps), ms, session_id[-6:])
    return plan


async def _planejar_com_pro(
    query: str,
    session_id: str,
    rota: str,
    dag_hint: dict,
    user_context: dict,
    history: str,
    fatos: list[str],
) -> ExecutionPlan:
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types

    prompt = f"""Rota detectada: {rota}
Dica do router: {json.dumps(dag_hint)}
Contexto do aluno: {json.dumps({k: user_context.get(k) for k in ("curso","centro","role") if user_context.get(k)})}
Fatos conhecidos: {"; ".join(fatos[:3]) if fatos else "nenhum"}
Histórico (últimas 2 trocas): {history[-300:] if history else "nenhum"}
Pergunta atual: "{query[:400]}"

Gere o plano de execução:"""

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = await client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,  # gemini-2.5-flash-lite ou pro conforme .env
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PLANNER,
            temperature=0.0,
            max_output_tokens=300,
            response_mime_type="application/json",
        ),
    )

    usage = response.usage_metadata
    if usage:
        _PRO_TOKENS.labels(direction="input").inc(usage.prompt_token_count or 0)
        _PRO_TOKENS.labels(direction="output").inc(usage.candidates_token_count or 0)

    data = json.loads(response.text or "{}")
    steps = data.get("steps", [])

    # Validação mínima
    if not steps or not isinstance(steps, list):
        raise ValueError("Plano vazio ou inválido retornado pelo Pro")

    valid_workers = {"rag_search", "synthesis", "crud_confirm", "greeting"}
    for step in steps:
        if step.get("worker") not in valid_workers:
            raise ValueError(f"Worker desconhecido: {step.get('worker')}")

    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        rota=rota,
        steps=steps,
        context={
            "query": query,
            "user_context": user_context,
            "history": history[-500:] if history else "",
            "fatos": fatos[:5],
        },
    )


def _plano_padrao_rag(
    rota: str, dag_hint: dict, query: str, session_id: str,
    user_context: dict, history: str, fatos: list[str],
) -> ExecutionPlan:
    """Plano RAG padrão sem chamar LLM — fallback seguro."""
    doc_type = dag_hint.get("doc_type", "geral")
    k = dag_hint.get("k", 6)
    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        rota=rota,
        steps=[
            {"id": "s1", "worker": "rag_search",
             "args": {"doc_type": doc_type, "k": k}, "depends_on": []},
            {"id": "s2", "worker": "synthesis",
             "args": {"max_tokens": 512}, "depends_on": ["s1"]},
        ],
        context={
            "query": query,
            "user_context": user_context,
            "history": history[-500:] if history else "",
            "fatos": fatos[:5],
        },
    )


def _plano_simples(
    rota: str, query: str, session_id: str,
    user_context: dict, history: str, fatos: list[str],
) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        rota=rota,
        steps=[{"id": "s1", "worker": "greeting", "args": {}, "depends_on": []}],
        context={"query": query, "user_context": user_context,
                 "history": history, "fatos": fatos},
    )


def _plano_crud(
    query: str, session_id: str,
    user_context: dict, history: str, fatos: list[str],
) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        rota="CRUD",
        steps=[{
            "id": "s1", "worker": "crud_confirm",
            "args": {"action": "detectar_crud", "description": query[:100]},
            "depends_on": [],
        }],
        context={"query": query, "user_context": user_context,
                 "history": history, "fatos": fatos},
    )

def _plano_media(
    query: str, session_id: str,
    user_context: dict, history: str, fatos: list[str], dag_hint: dict
) -> ExecutionPlan:
    worker = dag_hint["steps"][0]
    url = dag_hint.get("url", query)
    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        rota="MEDIA_DOWNLOAD",
        steps=[{"id": "s1", "worker": worker, "args": {"url": url}, "depends_on": []}],
        context={"query": query, "user_context": user_context,
                 "history": history, "fatos": fatos},
    )