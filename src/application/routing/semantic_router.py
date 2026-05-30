"""
src/application/routing/semantic_router.py
===========================================
Semantic Router — A Borda do Cognitive OS.

FLUXO:
  1. SemanticCache.get() → HIT → retorna imediatamente (0 tokens)
  2. MISS → Gemini Flash classifica intenção (< 50 tokens)
  3. Publica evento no Redis Stream para o barramento
  4. Retorna RouterDecision com rota + confiança + dag_hint

MÉTRICAS PROMETHEUS:
  oraculo_router_cache_hit_total{layer}
  oraculo_router_gemini_flash_tokens_total
  oraculo_router_latency_ms (histogram)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# ── Métricas ──────────────────────────────────────────────────────────────────
_CACHE_HIT = Counter(
    "oraculo_router_cache_hit_total",
    "Cache hits no router por camada",
    ["layer"],
)
_FLASH_TOKENS = Counter(
    "oraculo_router_gemini_flash_tokens_total",
    "Tokens consumidos pelo Gemini Flash no router",
    ["direction"],
)
_LATENCY = Histogram(
    "oraculo_router_latency_ms",
    "Latência do router em ms",
    buckets=[5, 10, 25, 50, 100, 250, 500],
)

# ── Rotas válidas ──────────────────────────────────────────────────────────────
ROTAS_VALIDAS = frozenset({
    "CALENDARIO", "EDITAL", "CONTATOS", "WIKI", "CRUD", "GREETING", "GERAL"
})

# ── Prompt zero-shot para Flash ────────────────────────────────────────────────
_SYSTEM_ROUTER = """Você é um classificador de intenções para o Oráculo UEMA.
Classifique a mensagem em EXATAMENTE uma destas rotas:
- CALENDARIO: datas, prazos, matrícula, semestre, feriados, início/fim de aulas
- EDITAL: PAES, vagas, cotas (AC, BR-PPI, PcD), vestibular, inscrição
- CONTATOS: emails, telefones, setores (PROG, CTIC, CECEN, reitoria, coordenação)
- WIKI: SIGAA, senha, wifi, suporte TI, sistemas, laboratórios
- CRUD: alterar/atualizar dados pessoais do próprio usuário
- GREETING: saudação pura sem pergunta (oi, obrigado, ok)
- GERAL: fora do escopo UEMA ou ambíguo

Responda APENAS com JSON: {"rota": "ROTA", "confianca": 0.0_a_1.0, "motivo": "max 60 chars"}"""


@dataclass
class RouterDecision:
    rota: str
    confianca: float
    motivo: str
    cache_hit: bool
    cache_layer: str   # "exact" | "semantic" | "miss"
    latencia_ms: int
    dag_hint: dict     # dica para o Planner montar o DAG


async def rotear(
    query: str,
    session_id: str,
    user_context: dict | None = None,
) -> RouterDecision:
    """
    Entry point do router. Thread-safe, stateless.
    """
    t0 = time.monotonic()
    ctx = user_context or {}

    # APAGAMOS O CACHE PROBLEMÁTICO AQUI! Vai direto para o Flash.

    # ── Passo B: Gemini Flash classifica ─────────────────────────────────────
    decision = await _classificar_com_flash(query, ctx)

    ms = int((time.monotonic() - t0) * 1000)
    _LATENCY.observe(ms)
    decision.latencia_ms = ms
    return decision


async def _classificar_com_flash(query: str, ctx: dict) -> RouterDecision:
    """Usa Gemini Flash para classificação zero-shot."""
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types

    ctx_str = f"Aluno de {ctx['curso']}" if ctx.get("curso") else ""
    prompt = f"{ctx_str}\nMensagem: \"{query[:300]}\"\nClassifique:"

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model="gemini-3.1-flash-lite-preview",   # 🔥 MODELO CORRIGIDO AQUI
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_ROUTER,
                temperature=0.0,
                max_output_tokens=80,
                response_mime_type="application/json",
            ),
        )

        # Métricas de tokens
        usage = response.usage_metadata
        if usage:
            _FLASH_TOKENS.labels(direction="input").inc(usage.prompt_token_count or 0)
            _FLASH_TOKENS.labels(direction="output").inc(usage.candidates_token_count or 0)

        # 🔥 CORREÇÃO DO JSON MARKDOWN (Evita o Expecting value line 1 column 1)
        texto = response.text.strip()
        if texto.startswith("```json"):
            texto = texto[7:-3].strip()
        elif texto.startswith("```"):
            texto = texto[3:-3].strip()

        data = json.loads(texto or "{}")
        
        rota = data.get("rota", "GERAL").upper()
        if rota not in ROTAS_VALIDAS:
            rota = "GERAL"
            
        confianca = float(data.get("confianca", 0.5))
        motivo = str(data.get("motivo", ""))[:60]

        logger.info("🧭 [ROUTER] Flash: rota=%s conf=%.2f | '%.40s'", rota, confianca, query)

        return RouterDecision(
            rota=rota, confianca=confianca, motivo=motivo,
            cache_hit=False, cache_layer="miss", latencia_ms=0,
            dag_hint=_dag_hint_para_rota(rota),
        )

    except Exception as e:
        logger.error("❌ [ROUTER] Flash falhou, usando fallback regex: %s", e)
        rota = _regex_fallback(query)
        return RouterDecision(
            rota=rota, confianca=0.4, motivo=f"regex_fallback: {type(e).__name__}",
            cache_hit=False, cache_layer="miss", latencia_ms=0,
            dag_hint=_dag_hint_para_rota(rota),
        )



def _regex_fallback(query: str) -> str:
    """Fallback de último recurso quando Flash falha."""
    import re
    q = query.lower()
    if re.search(r"matr[íi]cula|calend|prazo|semestre|trancamento|aula", q):
        return "CALENDARIO"
    if re.search(r"paes|vestibular|vaga|cota|inscri|edital", q):
        return "EDITAL"
    if re.search(r"email|telefone|contato|ctic\b|prog\b|reitoria", q):
        return "CONTATOS"
    if re.search(r"sigaa|senha|wifi|sistema|suporte|laborat", q):
        return "WIKI"
    return "GERAL"


def _dag_hint_para_rota(rota: str) -> dict:
    """
    Retorna dica de DAG para o Planner.
    Define quais workers devem ser ativados e em que ordem.
    """
    _HINTS = {
        "CALENDARIO":  {"steps": ["rag_search"], "doc_type": "calendario", "k": 8},
        "EDITAL":      {"steps": ["rag_search"], "doc_type": "edital",     "k": 10},
        "CONTATOS":    {"steps": ["rag_search"], "doc_type": "contatos",   "k": 6},
        "WIKI":        {"steps": ["rag_search"], "doc_type": "wiki_ctic",  "k": 6},
        "CRUD":        {"steps": ["crud_confirm"],                         "k": 0},
        "GREETING":    {"steps": ["greeting"],                             "k": 0},
        "GERAL":       {"steps": ["rag_search"], "doc_type": "geral",      "k": 6},
    }
    return _HINTS.get(rota, _HINTS["GERAL"])