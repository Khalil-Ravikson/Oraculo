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
from pydantic import BaseModel, Field

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

# ── Configurações ─────────────────────────────────────────────────────────────
MAX_TOKENS_POR_ROTA = {
    "SIGAA": 1024,
    "EDITAL": 800,
}


# ── Prompt do Planner ─────────────────────────────────────────────────────────
_SYSTEM_PLANNER = """<system_instruction>
Você é o Planner (Agente de Planejamento) do Oráculo UEMA.
Sua única responsabilidade é decompor uma intenção de usuário em um plano de execução estruturado (DAG de tarefas).

<workers_disponiveis>
- "rag_search": Busca híbrida de informações no Redis Vector Store. Args: {doc_type: str, k: int}
  - "doc_type" válidos: "calendario", "edital", "contatos", "wiki_ctic", "geral" (use "geral" como padrão).
  - "k": quantidade de documentos para retornar (use de 5 a 10).
- "synthesis": Gera a resposta final em linguagem natural sintetizando os resultados. Args: {max_tokens: int, tone: str?}
  - "max_tokens": tamanho máximo da resposta (padrão: 512).
  - "tone": tom da resposta (opcional: "gentil", "formal").
- "crud_confirm": Solicita confirmação manual (HITL) para alteração de dados cadastrais. Args: {action: str, description: str}
- "greeting": Responde saudações, agradecimentos ou meta-perguntas de capacidades imediatamente. Args: {}
</workers_disponiveis>

<regras_de_planejamento>
1. Dependências: O worker "synthesis" deve SEMPRE ter em "depends_on" o identificador do step "rag_search" correspondente (ex: se "rag_search" é "s1", "synthesis" deve ser "s2" com depends_on=["s1"]).
2. Restrição: Não invente outros workers. Use estritamente a lista acima.
3. Rota GREETING: Gere apenas um step com worker "greeting" (sem synthesis subsequente).
4. Rota CRUD: Gere apenas um step com worker "crud_confirm".
5. Consultas RAG Clássicas: Crie um plano com dois steps sequenciais: s1 ("rag_search") e s2 ("synthesis", dependendo de "s1").
6. Responda estritamente com um objeto JSON válido, sem cercas de código markdown (```json) e sem comentários.
</regras_de_planejamento>
</system_instruction>"""


class StepArgsSchema(BaseModel):
    doc_type: str | None = Field(default=None, description="Tipo de documento (ex: 'calendario', 'edital')")
    k: int | None = Field(default=None, description="Quantidade de chunks para retornar")
    max_tokens: int | None = Field(default=None, description="Quantidade maxima de tokens na sintese")
    tone: str | None = Field(default=None, description="Tom da resposta (ex: 'gentil', 'formal')")
    action: str | None = Field(default=None, description="Acao a ser executada no CRUD")
    description: str | None = Field(default=None, description="Descricao amigavel da operacao CRUD")


class PlanStepSchema(BaseModel):
    id: str = Field(description="Identificador unico do passo (ex: 's1', 's2')")
    worker: str = Field(description="Nome do worker (ex: 'rag_search', 'synthesis', 'crud_confirm', 'greeting')")
    args: StepArgsSchema = Field(default_factory=StepArgsSchema, description="Argumentos especificos passados para o worker")
    depends_on: list[str] = Field(default_factory=list, description="Lista de IDs de passos dos quais este depende")


class ExecutionPlanSchema(BaseModel):
    steps: list[PlanStepSchema] = Field(description="Lista de passos do plano em formato DAG")


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

    if rota == "SIGAA":
        plan = _plano_sigaa(query, session_id, user_context, history, fatos or [], dag_hint)
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

    # ── Enforce max tokens por rota para o Synthesis ──
    max_t = MAX_TOKENS_POR_ROTA.get(rota.upper(), 512)
    for step in plan.steps:
        if step.get("worker") == "synthesis":
            if "args" not in step:
                step["args"] = {}
            step["args"]["max_tokens"] = max_t

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
Fatos conhecidos: {"; ".join(fatos[:5]) if fatos else "nenhum"}
Histórico (últimas 2 trocas): {history[-1500:] if history else "nenhum"}
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
            response_schema=ExecutionPlanSchema,
        ),
    )

    usage = response.usage_metadata
    if usage:
        _PRO_TOKENS.labels(direction="input").inc(usage.prompt_token_count or 0)
        _PRO_TOKENS.labels(direction="output").inc(usage.candidates_token_count or 0)
        if session_id:
            from src.infrastructure.redis_client import registrar_tokens_redis
            registrar_tokens_redis(session_id, usage.prompt_token_count or 0, usage.candidates_token_count or 0)

    texto = (response.text or "").strip()
    if texto.startswith("```json"):
        texto = texto[7:-3].strip()
    elif texto.startswith("```"):
        texto = texto[3:-3].strip()

    data = json.loads(texto or "{}")
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
            "history": history[-1500:] if history else "",
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
            "history": history[-1500:] if history else "",
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

def _plano_sigaa(
    query: str, session_id: str,
    user_context: dict, history: str, fatos: list[str], dag_hint: dict
) -> ExecutionPlan:
    worker = dag_hint.get("worker", "sigaa_biblioteca")
    args = dag_hint.get("args", {})
    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        rota="SIGAA",
        steps=[{"id": "s1", "worker": worker, "args": args, "depends_on": []}],
        context={"query": query, "user_context": user_context,
                 "history": history, "fatos": fatos},
    )