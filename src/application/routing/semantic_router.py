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
import re
from prometheus_client import Counter, Histogram
from pydantic import BaseModel, Field

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
    "CALENDARIO", "EDITAL", "CONTATOS", "WIKI", "CRUD", "GREETING", "GERAL", "MEDIA_DOWNLOAD", "SIGAA"
})

_REGEX_CACHE: dict[str, re.Pattern] = {}

def _obter_intent_config(r: Any, nome: str) -> dict:
    try:
        raw = r.hget("router:config", nome)
        if raw:
            cfg = json.loads(raw if isinstance(raw, str) else raw.decode())
            return {
                "doc_type": cfg.get("doc_type", "geral"),
                "k_vector": cfg.get("k_vector", 6),
                "k_text":   cfg.get("k_text", 8),
            }
    except Exception:
        pass
    return {"doc_type": "geral", "k_vector": 6, "k_text": 8}

_RE_GREETING = re.compile(
    r'^(oi|olá|ola|bom\s?dia|boa\s?tarde|boa\s?noite|hey|hi|hello|'
    r'tudo\s?bem|e\s?aí|eai|opa|obrigad[ao]|valeu|vlw|tmj|ok|certo|👍|🙏|perfeito)\s*[!.?]*$',
    re.I | re.UNICODE,
)

_RE_YTB = re.compile(r'(https?://(?:www\.)?youtu(?:be\.com/watch\?v=|\.be/)[\w\-]+)', re.I)
_RE_INSTA = re.compile(r'(https?://(?:www\.)?instagram\.com/(?:p|reel)/[\w\-]+)', re.I)
_RE_SIGAA = re.compile(
    r'(sigaa|biblioteca|acervo|livro|obra|marc|inscrever|inscrição|processo seletivo|edital sigaa|concurso uema)',
    re.I
)

def _regex_rapido(query: str) -> str | None:
    """Layer 1: Fast-path regex ANTES do Flash — economiza ~50 tokens."""
    if _RE_YTB.search(query):
        return "MEDIA_DOWNLOAD"
    if _RE_INSTA.search(query):
        return "MEDIA_DOWNLOAD"
    if _RE_GREETING.match(query.strip()):
        return "GREETING"
    if _RE_SIGAA.search(query):
        return "SIGAA"
    return None

def _heuristica_basica(query: str) -> str | None:
    """Layer 2: Padrões comuns heurísticos."""
    q = query.lower()
    if "sigaa" in q and "senha" in q:
        return "WIKI"
    if "calendário" in q or "calendario" in q:
        return "CALENDARIO"
    return None

class RoutingDecision(BaseModel):
    """Esquema Pydantic para validação estruturada da decisão de roteamento pelo Gemini."""
    rota: str = Field(description="A rota: CALENDARIO, EDITAL, CONTATOS, WIKI, CRUD, GREETING, ou GERAL")
    confianca: float = Field(description="Nível de certeza da decisão (0.0 a 1.0)")
    motivo: str = Field(description="Justificativa breve da decisão (máx 60 caracteres)")

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
    t0 = time.monotonic()
    ctx = user_context or {}

    # ── Layer 1: Fast-path (Hardcoded Regex) ─────────
    rota_rapida = _regex_rapido(query)
    if rota_rapida:
        ms = int((time.monotonic() - t0) * 1000)
        _LATENCY.observe(ms)
        _CACHE_HIT.labels(layer="regex_l1").inc()
        return RouterDecision(
            rota=rota_rapida, confianca=0.99, motivo="layer_1_regex",
            cache_hit=True, cache_layer="regex", latencia_ms=ms,
            dag_hint=_dag_hint_para_rota(rota_rapida, query),
        )

    # ── Layer 2: Heurística Básica ─────────
    rota_heuristica = _heuristica_basica(query)
    if rota_heuristica:
        ms = int((time.monotonic() - t0) * 1000)
        _LATENCY.observe(ms)
        _CACHE_HIT.labels(layer="regex_l2").inc()
        return RouterDecision(
            rota=rota_heuristica, confianca=0.85, motivo="layer_2_heuristic",
            cache_hit=True, cache_layer="regex", latencia_ms=ms,
            dag_hint=_dag_hint_para_rota(rota_heuristica, query),
        )

    # ── Layer 3: Redis-Seeded Regex (Dinamico) ─────────
    from src.infrastructure.redis_client import get_redis_text
    r_text = get_redis_text()
    try:
        todas_regex = r_text.hgetall("router:regex")
        if todas_regex:
            q_lower = query.lower().strip()
            for nome_raw, regex_raw in todas_regex.items():
                nome = nome_raw if isinstance(nome_raw, str) else nome_raw.decode()
                regex = regex_raw if isinstance(regex_raw, str) else regex_raw.decode()
                if not regex:
                    continue
                if nome not in _REGEX_CACHE:
                    try:
                        _REGEX_CACHE[nome] = re.compile(regex, re.IGNORECASE)
                    except re.error:
                        continue
                if _REGEX_CACHE[nome].search(q_lower):
                    cfg = _obter_intent_config(r_text, nome)
                    ms = int((time.monotonic() - t0) * 1000)
                    _LATENCY.observe(ms)
                    _CACHE_HIT.labels(layer="regex_seeded").inc()
                    return RouterDecision(
                        rota=nome, confianca=0.95, motivo="seeded_regex_match",
                        cache_hit=True, cache_layer="regex", latencia_ms=ms,
                        dag_hint=_dag_hint_para_rota(nome, query, cfg),
                    )
    except Exception as e:
        logger.debug("Falha no roteamento por regex dinâmico: %s", e)

    # ── Layer 4: Redis KNN Search (Dinamico) ─────────
    try:
        from src.infrastructure.redis_client import get_redis, IDX_TOOLS
        from src.rag.embeddings import get_embeddings
        import numpy as np
        from redis.commands.search.query import Query as RQuery
        import asyncio

        r_bytes = get_redis()
        emb = get_embeddings()
        vetor = await asyncio.to_thread(emb.embed_query, query.lower().strip())
        vetor_bytes = np.array(vetor, dtype=np.float32).tobytes()

        q_knn = (
            RQuery("*=>[KNN 1 @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("name", "score")
            .dialect(2)
            .paging(0, 1)
        )
        res = r_bytes.ft(IDX_TOOLS).search(q_knn, {"vec": vetor_bytes})
        if res.docs:
            doc = res.docs[0]
            distancia = float(getattr(doc, "score", 1.0))
            similarity = max(0.0, 1.0 - distancia)
            if similarity >= 0.82:
                nome_raw = getattr(doc, "name", "GERAL")
                nome = nome_raw if isinstance(nome_raw, str) else nome_raw.decode()
                cfg = _obter_intent_config(r_text, nome)
                ms = int((time.monotonic() - t0) * 1000)
                _LATENCY.observe(ms)
                _CACHE_HIT.labels(layer="knn_seeded").inc()
                return RouterDecision(
                    rota=nome, confianca=round(0.90 * similarity, 2), motivo="seeded_knn_match",
                    cache_hit=True, cache_layer="semantic", latencia_ms=ms,
                    dag_hint=_dag_hint_para_rota(nome, query, cfg),
                )
    except Exception as e:
        logger.debug("Falha no roteamento por KNN dinâmico: %s", e)

    # ── Layer 5: Flash (LLM) ──────────────────────
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
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_ROUTER,
                temperature=0.0,
                max_output_tokens=80,
                response_mime_type="application/json",
                response_schema=RoutingDecision,
            ),
        )

        # Métricas de tokens
        usage = response.usage_metadata
        if usage:
            _FLASH_TOKENS.labels(direction="input").inc(usage.prompt_token_count or 0)
            _FLASH_TOKENS.labels(direction="output").inc(usage.candidates_token_count or 0)

        # Parsing seguro do JSON
        texto = response.text.strip()
        if texto.startswith("```json"):
            texto = texto[7:-3].strip()
        elif texto.startswith("```"):
            texto = texto[3:-3].strip()

        data = json.loads(texto or "{}")
        
        # Validação via Pydantic schema
        decision_validated = RoutingDecision(**data)
        
        rota = decision_validated.rota.upper()
        if rota not in ROTAS_VALIDAS:
            # Também aceita se existir no Redis config (intents semeadas dinamicamente)
            from src.infrastructure.redis_client import get_redis_text
            r_text = get_redis_text()
            if not r_text.hexists("router:config", rota):
                rota = "GERAL"
            
        confianca = float(decision_validated.confianca)
        motivo = str(decision_validated.motivo)[:60]

        logger.info("🧭 [ROUTER] Flash: rota=%s conf=%.2f | '%.40s'", rota, confianca, query)

        return RouterDecision(
            rota=rota, confianca=confianca, motivo=motivo,
            cache_hit=False, cache_layer="miss", latencia_ms=0,
            dag_hint=_dag_hint_para_rota(rota, query),
        )

    except Exception as e:
        logger.error("❌ [ROUTER] Flash falhou, usando fallback regex: %s", e)
        rota = _regex_fallback(query)
        return RouterDecision(
            rota=rota, confianca=0.4, motivo=f"regex_fallback: {type(e).__name__}",
            cache_hit=False, cache_layer="miss", latencia_ms=0,
            dag_hint=_dag_hint_para_rota(rota, query),
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


def _dag_hint_para_rota(rota: str, query: str = "", config: dict | None = None) -> dict:
    """
    Retorna dica de DAG para o Planner.
    Define quais workers devem ser ativados e em que ordem.
    """
    if rota == "MEDIA_DOWNLOAD":
        match_ytb = _RE_YTB.search(query)
        if match_ytb:
            return {"steps": ["ytb_download"], "url": match_ytb.group(1)}
        match_insta = _RE_INSTA.search(query)
        if match_insta:
            return {"steps": ["insta_download"], "url": match_insta.group(1)}
        return {"steps": ["ytb_download"], "url": query}

    if rota == "SIGAA":
        from src.application.use_cases.sigaa_use_cases import SIGAAUseCase
        uc = SIGAAUseCase()
        fluxo = uc.detectar_fluxo(query)
        if fluxo:
            return {"steps": [fluxo["worker"]], "worker": fluxo["worker"], "args": fluxo["args"]}
        return {"steps": ["sigaa_biblioteca"], "worker": "sigaa_biblioteca", "args": {}}

    if config:
        doc_type = config.get("doc_type", "geral")
        return {
            "steps": ["greeting"] if doc_type == "greeting" else ["rag_search"],
            "doc_type": doc_type,
            "k_vector": config.get("k_vector", 6),
            "k_text": config.get("k_text", 8),
        }

    _HINTS = {
        "CALENDARIO":  {"steps": ["rag_search"], "doc_type": "calendario", "k": 8},
        "EDITAL":      {"steps": ["rag_search"], "doc_type": "edital",     "k": 10},
        "CONTATOS":    {"steps": ["rag_search"], "doc_type": "contatos",   "k": 6},
        "WIKI":        {"steps": ["rag_search"], "doc_type": "wiki_ctic",  "k": 6},
        "CRUD":        {"steps": ["crud_confirm"],                         "k": 0},
        "GREETING":    {"steps": ["greeting"],                             "k": 0},
        "GERAL":       {"steps": ["rag_search"], "doc_type": "geral",      "k": 6},
        "SIGAA":       {"steps": ["sigaa_biblioteca"],                     "k": 0},
    }
    return _HINTS.get(rota, _HINTS["GERAL"])