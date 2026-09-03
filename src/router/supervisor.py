"""
src/router/supervisor.py
===========================
Supervisor — o componente central de decisão do Oráculo (ver seção 0.1 do
PLANO_REFATORACAO_SUPERVISOR.md: este módulo *é* o Supervisor, não existe uma
camada extra acima dele).

FLUXO (5 camadas, mais rápida/barata primeiro):
  1. Regex rápido (hardcoded) — greeting, media download, SIGAA
  2. Heurística básica
  3. Regex semeado dinamicamente via Redis (`router:regex`)
  4. KNN vetorial semeado via Redis (`idx:tools`)
  5. Fallback Gemini Flash (`router/llm_fallback.py`), com validação Pydantic

MÉTRICAS PROMETHEUS:
  oraculo_router_cache_hit_total{layer}
  oraculo_router_latency_ms (histogram)

Ex-`application/routing/semantic_router.py` (mantido ali como shim de
compatibilidade — ver esse arquivo). Zero mudança de comportamento nesta
migração: a única alteração é a chamada Gemini ter sido extraída para
`router/llm_fallback.py`, e o import de `sigaa_use_cases` (usado em
`_dag_hint_para_rota` para a rota SIGAA) permanecer local à função para não
criar um acoplamento de import-time entre router e domain/use_cases.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from prometheus_client import Counter, Histogram

from src.router.contracts import ROTAS_VALIDAS, RouterDecision
from src.router.llm_fallback import _classificar_com_flash

logger = logging.getLogger(__name__)


def _get_or_create_metric(metric_cls, name, documentation, labelnames=(), **kwargs):
    """
    Evita 'Duplicated timeseries in CollectorRegistry': `PrometheusMetrics`
    (infrastructure/observability/metrics.py) registra `oraculo_router_latency_ms`/
    `oraculo_router_cache_hit_total` sob os MESMOS nomes, de forma segura
    (`_get_or_create`). Este módulo registrava direto via `Histogram(...)`/
    `Counter(...)`, sem essa proteção — colidia sempre que `get_metrics()`
    rodasse antes deste módulo ser importado pela primeira vez (bug real
    encontrado na Sprint 1.3 do roadmap multimodal, ver notas.md seção 11:
    STT agora chama `get_metrics()` bem cedo no pipeline, antes do primeiro
    `rotear()`, expondo uma dependência de ordem de import que já existia).
    """
    from prometheus_client import REGISTRY
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return metric_cls(name, documentation, labelnames=labelnames, **kwargs)


# ── Métricas ──────────────────────────────────────────────────────────────────
_CACHE_HIT = _get_or_create_metric(
    Counter, "oraculo_router_cache_hit_total",
    "Cache hits no router por camada", ["layer"],
)
_LATENCY = _get_or_create_metric(
    Histogram, "oraculo_router_latency_ms",
    "Latência do router em ms",
    buckets=[5, 10, 25, 50, 100, 250, 500],
)

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
# Busca por termo (sem URL) — verbo explícito "buscar/procurar/baixar/baixe" +
# "vídeo" pra não colidir com pergunta acadêmica real tipo "tem vídeo sobre
# isso?" (só vira comando de download com essa combinação específica de
# palavras). "baixar/baixe" adicionados nesta sessão — o orquestrador LLM já
# classificava "baixe vídeo de X" como call_media (intent correto), mas essa
# regex de extração de termo (usada tanto aqui quanto no Fast-Path de
# dispatcher.py) só reconhecia "buscar", então a mensagem inteira virava "url"
# e o yt-dlp falhava com "not a valid URL" — achado real testando ao vivo.
_RE_YTB_BUSCA = re.compile(
    r'\b(?:buscar|procurar|baixar|baixe)\s+(?:um\s+)?v[ií]deo\b(?:\s+(?:sobre|de|do|da))?\s+(.+)',
    re.I,
)
_RE_SIGAA = re.compile(
    r'(sigaa|biblioteca|acervo|livro|obra|marc|inscrever|inscrição|processo seletivo|edital sigaa|concurso uema|nota|média|cr\b|ira\b|histórico|turmas|grade|matéria|integraliza|grade curricular|estrutura curricular|sala|professor|complementar)',
    re.I
)
# Abertura de chamado/ticket em linguagem livre (item 1 da rodada de testes de
# ponta-a-ponta — antes só alcançável via !atualizaremail). Mesmo padrão do
# regex de SIGAA acima: fast-path L1, sem gastar tokens do Flash.
_RE_TICKET_ABERTURA = re.compile(
    r'(abrir?\s+(um\s+)?(ticket|chamado)|preciso\s+(de\s+)?(um\s+)?(ticket|chamado)|'
    r'quero\s+(abrir|fazer)\s+(um\s+)?(ticket|chamado)|registrar\s+(um\s+)?chamado|'
    r'suporte\s+t[ée]cnico|problema\s+(no|com)\s+.*sistema)',
    re.I
)
# Layer 0 (fusão Router+Orquestrador, ver notas.md §5.1): heurística barata pra
# "e aí, já saiu?" tipo de pergunta — sem LLM, mas só dispara quando existe uma
# tarefa recente (`task_history.last_worker`) pra não colidir com pergunta nova.
# Preserva a precedência que o antigo orchestrate() tinha (contexto de memória
# vencendo ANTES do regex de conteúdo L1) sem pagar chamada LLM pra isso.
_RE_CHECK_STATUS = re.compile(
    r'\b(j[áa]\s+(saiu|saíram|ficou|ficaram|terminou|terminaram|deu)|status|'
    r'andamento|resultado\s+d[ao]|como\s+ficou|deu\s+certo|conseguiu|'
    r'aquilo\s+que\s+(eu\s+)?pedi)\b',
    re.I,
)
# Nomes de sistemas institucionais cobertos pelo wiki CTIC (mesmos hubs
# curados em `KNOWN_SYSTEM_HUBS`, infrastructure/scraping/implementations/
# dokuwiki/hierarchy.py) mas SEM cobertura em nenhuma camada L1-L4 —
# achado real: "Sipac" sozinho caía sempre no Flash (L5), com resultado
# não-determinístico de mensagem pra mensagem (WIKI/GREETING/GERAL
# alternando pra texto idêntico, achado analisando log de produção).
# Rota WIKI (não SIGAA — esses sistemas não são o portal do aluno).
_RE_SISTEMA_WIKI = re.compile(r'\b(sipac|siguema)\b', re.I)

# Pedido explícito de atendimento humano (ADR 0008, Fase 2) — L1 pra não
# gastar token do Flash num pedido inequívoco. Só frases claras: "atendente"
# sozinho num contexto acadêmico não conta (ex.: "quem é o atendente da
# biblioteca?" cai no RAG normal), por isso exige o verbo de contato.
_RE_HUMANO = re.compile(
    r'\b(falar|conversar|transferir?|me\s+passa)\b.{0,20}\bcom\s+(um[a]?\s+)?'
    r'(algu[ée]m|pessoa|gente|atendente|humano|respons[áa]vel)\b|'
    r'\b(quero|preciso|queria)\b.{0,15}\b(um[a]?\s+)?(atendente|humano)\b|'
    r'\batend(?:ente|imento)\s+humano\b|\bpessoa\s+de\s+verdade\b|'
    r'\bn[ãa]o\s+quero\s+(falar\s+com\s+)?(rob[ôo]|bot|m[áa]quina|assistente)\b',
    re.I,
)


def _regex_rapido(query: str) -> str | None:
    """Layer 1: Fast-path regex ANTES do Flash — economiza ~50 tokens."""
    if _RE_HUMANO.search(query):
        return "ESCALAR_HUMANO"
    if _RE_YTB.search(query):
        return "MEDIA_DOWNLOAD"
    if _RE_INSTA.search(query):
        return "MEDIA_DOWNLOAD"
    if _RE_YTB_BUSCA.search(query):
        return "MEDIA_DOWNLOAD"
    if _RE_GREETING.match(query.strip()):
        return "GREETING"
    if _RE_TICKET_ABERTURA.search(query):
        return "TICKET_ABERTURA"
    if _RE_SIGAA.search(query):
        return "SIGAA"
    if _RE_SISTEMA_WIKI.search(query):
        return "WIKI"
    return None


def _heuristica_basica(query: str) -> str | None:
    """Layer 2: Padrões comuns heurísticos."""
    q = query.lower()
    if "sigaa" in q and "senha" in q:
        return "WIKI"
    if "calendário" in q or "calendario" in q:
        return "CALENDARIO"
    return None


async def rotear(
    query: str,
    session_id: str,
    user_context: dict | None = None,
) -> RouterDecision:
    t0 = time.monotonic()
    ctx = user_context or {}

    # ── Layer 0: Heurística de CHECK_STATUS (barata, sem LLM) ─────────
    # Roda ANTES do L1 pra não perder pra um regex de conteúdo (ex: "notas"
    # bate em SIGAA) quando a pergunta é claramente um follow-up de tarefa
    # recente — mesma precedência que o antigo orchestrate() tinha, achado
    # numa revisão crítica desta fusão (ver notas.md §5.1).
    if session_id:
        try:
            from src.memory.services.redis_memory_service import get_cognitive_memory
            task_history = await get_cognitive_memory().get_task_history(session_id)
        except Exception:
            task_history = {}
        if task_history.get("last_worker") and _RE_CHECK_STATUS.search(query):
            ms = int((time.monotonic() - t0) * 1000)
            _LATENCY.observe(ms)
            _CACHE_HIT.labels(layer="check_status_heuristic").inc()
            return RouterDecision(
                rota="CHECK_STATUS", confianca=0.85, motivo="layer_0_status_heuristic",
                cache_hit=True, cache_layer="regex", latencia_ms=ms,
                dag_hint=_dag_hint_para_rota("CHECK_STATUS", query),
            )

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
    _CACHE_HIT.labels(layer="flash_fallback").inc()
    decision = await _classificar_com_flash(query, ctx, session_id)
    ms = int((time.monotonic() - t0) * 1000)
    _LATENCY.observe(ms)
    decision.latencia_ms = ms
    return decision


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
        match_busca = _RE_YTB_BUSCA.search(query)
        if match_busca:
            # "ytsearch1:" é o extractor de busca nativo do yt-dlp — devolve
            # o 1º resultado, sem precisar de API/chave extra nem servidor
            # MCP externo. `_plano_media` (planning.py) só lê a chave "url"
            # do dag_hint, então isso reaproveita o mesmo caminho de sempre.
            return {"steps": ["ytb_download"], "url": f"ytsearch1:{match_busca.group(1).strip()}"}
        return {"steps": ["ytb_download"], "url": query}

    if rota == "TICKET_ABERTURA":
        # Fast-path próprio em dispatcher.py (agents/tickets/ticket_flow.py) —
        # nunca chega ao Planner (ver agents/tickets/service.py pro histórico
        # de por que essa rota nunca despacha um worker via Planner).
        return {"steps": ["ticket_abertura"]}

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

    # Plano A / Fase 2: o `_HINTS` hardcoded virou as colunas
    # `planner_steps`/`doc_type`/`k` do `route_registry` (migration 010). O
    # branch `if config:` acima (router:config, de `intents_router`) continua
    # tendo precedência — este é só o fallback, para rota não semeada em
    # `router:config` (e para CRUD/CHECK_STATUS, que o dispatcher intercepta
    # antes do Planner). Rota desconhecida → default de GERAL.
    from src.infrastructure import route_registry
    rr = route_registry.get(rota)
    hint: dict = {"steps": list(rr.planner_steps or [])}
    if rr.doc_type:
        hint["doc_type"] = rr.doc_type
    if rr.k is not None:
        hint["k"] = rr.k
    return hint
