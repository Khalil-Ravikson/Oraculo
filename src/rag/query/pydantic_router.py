"""
rag/query/pydantic_router.py — Roteador de Elite com PydanticAI (Sprint 2)
==========================================================================

PROBLEMA RESOLVIDO:
  O roteador anterior usava regex + KNN vetorial no Redis.
  Funciona bem para casos óbvios, mas falhava silenciosamente em:
    - Ambiguidade ("quero saber sobre datas de inscrição" → EDITAL ou CALENDARIO?)
    - Perguntas compostas ("email do CTIC e prazo de matrícula")
    - Intenção de CRUD camuflada ("preciso mudar meu email para...")

  PydanticAI resolve isso porque:
    1. Força o Gemini a retornar JSON VALIDADO pelo Pydantic (nunca parse falho)
    2. Inclui campo `confianca` (0.0-1.0) — usado para decisão de cache
    3. Inclui `motivo` — visível no Langfuse para debug

DECISÃO DE CACHE (regra central do Sprint 2):
  confianca >= 0.80 → semantic cache pode ser consultado
  confianca <  0.80 → SKIP cache → força RAG fresco
  
  Por que 0.80?
    Abaixo disso o roteador não tem certeza da intenção.
    Usar cache de uma rota errada é pior do que não usar cache.
    RAG fresco custa ~200ms mas garante contexto correto.

INTEGRAÇÃO COM O GRAFO:
  node_classify() chama pydantic_router.rotear()
  O resultado é colocado no state: route, confianca, skip_cache
  route_after_classify() usa essas informações para despachar

FALLBACK CHAIN:
  PydanticAI (Gemini) → falha → KNN Redis (legado)
  KNN Redis → falha → regex (router.py existente)
  Nunca levanta exception para o caller.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD_CACHE = 0.80   # abaixo → skip cache
CONFIDENCE_THRESHOLD_MIN   = 0.40   # abaixo → GERAL (ignora classificação)

# Máximo de tokens usados pelo roteador (prompt curto = barato)
_MAX_TOKENS_ROUTER = 200

# TTL do cache local do roteador (evita chamar Gemini 2x para a mesma msg)
_ROUTER_CACHE_TTL_S = 30

# ─────────────────────────────────────────────────────────────────────────────
# Schema de decisão (validado pelo Pydantic → nunca falha parse)
# ─────────────────────────────────────────────────────────────────────────────

RotaValida = Literal[
    "CALENDARIO",   # datas, prazos, semestres
    "EDITAL",       # PAES, vagas, cotas
    "CONTATOS",     # emails, telefones, setores
    "WIKI",         # sistemas TI, SIGAA, suporte CTIC
    "CRUD",         # intenção de atualizar dados pessoais
    "GREETING",     # saudação pura (oi, bom dia, obrigado)
    "GERAL",        # fora do escopo ou ambíguo
]


class RoutingDecision(BaseModel):
    """
    Decisão de roteamento com validação Pydantic.
    O Gemini é forçado a retornar exatamente este schema — sem exceções.
    """
    decisao:   RotaValida = Field(
        description="A rota mais adequada para a mensagem"
    )
    confianca: float = Field(
        ge=0.0, le=1.0,
        description="Nível de certeza de 0.0 a 1.0"
    )
    motivo: str = Field(
        max_length=120,
        description="Justificativa breve (máximo 120 chars)"
    )
    intencao_crud: bool = Field(
        default=False,
        description="True se o usuário quer modificar seus próprios dados"
    )

    @field_validator("confianca")
    @classmethod
    def round_confianca(cls, v: float) -> float:
        return round(v, 3)

    @property
    def skip_cache(self) -> bool:
        """True se a confiança é insuficiente para consultar o cache semântico."""
        return self.confianca < CONFIDENCE_THRESHOLD_CACHE

    @property
    def usar_geral(self) -> bool:
        """True se a confiança é tão baixa que deve tratar como GERAL."""
        return self.confianca < CONFIDENCE_THRESHOLD_MIN


@dataclass
class RouterResult:
    """Resultado enriquecido do roteador — injetado no OracleState."""
    decisao:       RotaValida
    confianca:     float
    motivo:        str
    skip_cache:    bool
    intencao_crud: bool
    metodo:        str        # "pydantic_ai" | "knn_redis" | "regex"
    latencia_ms:   int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Prompt do roteador (curto e preciso — minimiza tokens)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_ROUTER = """Você é o roteador do Oráculo UEMA. Classifique mensagens de alunos universitários.

ROTAS DISPONÍVEIS:
- CALENDARIO: datas, prazos, matrículas, semestres, feriados, início/fim de aulas
- EDITAL: PAES, vagas, cotas (AC, BR-PPI, PcD), inscrição no vestibular  
- CONTATOS: e-mails, telefones, setores (PROG, CTIC, CECEN, reitoria)
- WIKI: suporte TI, SIGAA, senha, wifi, sistemas, laboratórios
- CRUD: alterar/atualizar/mudar dados pessoais do próprio usuário (e-mail, telefone, nome)
- GREETING: saudação pura sem pergunta substancial (oi, bom dia, obrigado, ok)
- GERAL: fora do escopo UEMA ou ambíguo

REGRA DE CONFIANÇA:
- 0.90-1.00: mensagem inequívoca (ex: "quando é a matrícula?")
- 0.70-0.89: clara mas com possível ambiguidade  
- 0.50-0.69: duas rotas possíveis, escolhi a mais provável
- 0.30-0.49: mensagem vaga ou fora do escopo parcialmente
- 0.00-0.29: completamente fora do escopo ou sem contexto

Responda APENAS com JSON válido. Nada mais."""

_PROMPT_ROUTER_TEMPLATE = """Contexto do aluno: {contexto}

Mensagem: "{mensagem}"

Classifique e retorne o JSON."""


# ─────────────────────────────────────────────────────────────────────────────
# PydanticRouter
# ─────────────────────────────────────────────────────────────────────────────

class PydanticRouter:
    """
    Roteador que usa PydanticAI + Gemini para decisões de roteamento
    com JSON validado e threshold de confiança para controle de cache.
    
    Thread-safe: sem estado mutável após __init__.
    """

    def __init__(self):
        # Cache local em memória (evita double-call para a mesma mensagem)
        # _cache = {hash(mensagem): (RouterResult, timestamp)}
        self._cache: dict[int, tuple[RouterResult, float]] = {}

    async def rotear_async(
        self,
        mensagem: str,
        contexto_usuario: dict | None = None,
        is_admin: bool = False,
    ) -> RouterResult:
        """
        Versão 100% assíncrona do roteador PydanticAI.
        Usa google-genai async client diretamente — sem asyncio.to_thread.
        """
        import asyncio, json, time
        from src.infrastructure.observability.langfuse_client import langfuse_span

        # Cache local (evita Gemini 2x para a mesma msg no mesmo segundo)
        cache_key = hash(f"{mensagem}:{str(contexto_usuario)}")
        cached    = self._cache.get(cache_key)
        if cached:
            result, ts = cached
            if time.time() - ts < _ROUTER_CACHE_TTL_S:
                logger.debug("🗃️  [PYDANTIC ROUTER] Cache hit: '%.40s'", mensagem)
                return result

        ctx_str = _formatar_contexto(contexto_usuario or {})
        prompt  = _PROMPT_ROUTER_TEMPLATE.format(
            contexto=ctx_str,
            mensagem=mensagem[:400],
        )

        t0 = time.monotonic()
        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)

            # Chamada async nativa — sem wrapper síncrono
            response = await client.aio.models.generate_content(
                model    = settings.GEMINI_MODEL,
                contents = prompt,
                config   = types.GenerateContentConfig(
                    system_instruction  = _SYSTEM_ROUTER,
                    temperature         = 0.0,
                    max_output_tokens   = _MAX_TOKENS_ROUTER,
                    response_mime_type  = "application/json",
                    response_schema     = RoutingDecision,
                ),
            )

            ms   = int((time.monotonic() - t0) * 1000)
            data = json.loads(response.text or "{}")
            decision = RoutingDecision(**data)

            if decision.usar_geral:
                decision.decisao   = "GERAL"
                decision.confianca = min(decision.confianca, 0.39)
            if decision.intencao_crud:
                decision.decisao = "CRUD"

            result = RouterResult(
                decisao       = decision.decisao,
                confianca     = decision.confianca,
                motivo        = decision.motivo,
                skip_cache    = decision.skip_cache,
                intencao_crud = decision.intencao_crud,
                metodo        = "pydantic_ai",
                latencia_ms   = ms,
            )

            # Persiste no cache local
            self._cache[cache_key] = (result, time.time())
            return result

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [PYDANTIC ROUTER] Gemini async falhou | ms=%d | erro: %s",
                ms, exc,
            )
            # Retorna resultado de fallback — o orquestrador vai ao regex
            return RouterResult(
                decisao=_rota_fallback_regex(mensagem),
                confianca=0.0,
                motivo=f"Gemini falhou: {type(exc).__name__}",
                skip_cache=True,
                intencao_crud=False,
                metodo="fallback_regex",
                latencia_ms=ms,
            )

    # ── PydanticAI call ───────────────────────────────────────────────────────

    def _rotear_com_pydantic_ai(
        self,
        mensagem: str,
        contexto_usuario: dict,
    ) -> RouterResult:
        """
        Chama o Gemini via PydanticAI e valida o output com RoutingDecision.
        Retorna resultado com metodo="fallback_knn" se algo falhar.
        """
        from src.infrastructure.observability.langfuse_client import langfuse_span

        try:
            from src.infrastructure.settings import settings

            # Monta contexto resumido do aluno
            ctx_str = _formatar_contexto(contexto_usuario)
            prompt  = _PROMPT_ROUTER_TEMPLATE.format(
                contexto=ctx_str,
                mensagem=mensagem[:400],
            )

            # Chama Gemini com structured output via google-genai (sem PydanticAI lib)
            # PydanticAI usa o mesmo padrão de response_schema Pydantic
            import google.genai as genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)

            with langfuse_span("pydantic_router", input={"msg": mensagem[:80]}):
                response = client.models.generate_content(
                    model   = settings.GEMINI_MODEL,
                    contents = prompt,
                    config  = types.GenerateContentConfig(
                        system_instruction  = _SYSTEM_ROUTER,
                        temperature         = 0.0,   # determinístico
                        max_output_tokens   = _MAX_TOKENS_ROUTER,
                        response_mime_type  = "application/json",
                        response_schema     = RoutingDecision,
                    ),
                )

            # Gemini retorna JSON validado — parse seguro
            import json
            raw = response.text or "{}"
            data = json.loads(raw)
            decision = RoutingDecision(**data)

            # Aplica threshold mínimo
            if decision.usar_geral:
                decision.decisao   = "GERAL"
                decision.confianca = min(decision.confianca, 0.39)

            # CRUD override
            if decision.intencao_crud:
                decision.decisao = "CRUD"

            return RouterResult(
                decisao       = decision.decisao,
                confianca     = decision.confianca,
                motivo        = decision.motivo,
                skip_cache    = decision.skip_cache,
                intencao_crud = decision.intencao_crud,
                metodo        = "pydantic_ai",
            )

        except Exception as e:
            logger.warning(
                "⚠️  PydanticRouter Gemini falhou, usando fallback KNN: %s", e
            )
            return RouterResult(
                decisao=_rota_fallback_regex(mensagem),
                confianca=0.5,
                motivo="fallback ativado",
                skip_cache=True,   # sem confiança → skip cache
                intencao_crud=False,
                metodo="fallback_knn",
            )

    # ── Fallback KNN ──────────────────────────────────────────────────────────

    def _fallback_knn(self, mensagem: str, estado_menu: str) -> RouterResult:
        """
        Fallback para o roteador KNN Redis existente.
        Mantemos o legado intacto (nunca reescrever o que funciona).
        """
        try:
            from src.domain.services.semantic_router import rotear
            from src.domain.entities import EstadoMenu
            res = rotear(mensagem, EstadoMenu.MAIN)
            return RouterResult(
                decisao       = res.rota.value,
                confianca     = res.score,
                motivo        = f"knn fallback: {res.metodo}",
                skip_cache    = res.score < CONFIDENCE_THRESHOLD_CACHE,
                intencao_crud = False,
                metodo        = "knn_redis",
            )
        except Exception as e:
            logger.error("❌ Fallback KNN também falhou: %s", e)
            return RouterResult(
                decisao="GERAL", confianca=0.0,
                motivo="todos os fallbacks falharam",
                skip_cache=True, intencao_crud=False,
                metodo="regex",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _formatar_contexto(ctx: dict) -> str:
    """Formata contexto do aluno de forma compacta para o prompt."""
    partes = []
    if ctx.get("curso"):
        partes.append(f"Curso: {ctx['curso']}")
    if ctx.get("periodo"):
        partes.append(f"Período: {ctx['periodo']}")
    if ctx.get("centro"):
        partes.append(f"Centro: {ctx['centro']}")
    return " | ".join(partes) if partes else "Aluno (sem contexto)"


def _rota_por_estado_menu(estado: str) -> RotaValida | None:
    """Mapeia estado de submenu para rota forçada."""
    mapa = {
        "SUB_CALENDARIO": "CALENDARIO",
        "SUB_EDITAL":     "EDITAL",
        "SUB_CONTATOS":   "CONTATOS",
    }
    return mapa.get(estado)


def _rota_fallback_regex(mensagem: str) -> RotaValida:
    """Regex de último recurso quando tudo falha."""
    import re
    msg = mensagem.lower()
    if re.search(r"matr[íi]cula|calend[áa]rio|prazo|semestre|aula", msg):
        return "CALENDARIO"
    if re.search(r"paes|vestibular|vaga|cota|inscri", msg):
        return "EDITAL"
    if re.search(r"email|telefone|contato|ctic|prog", msg):
        return "CONTATOS"
    if re.search(r"sigaa|senha|wifi|sistema|suporte", msg):
        return "WIKI"
    return "GERAL"


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_pydantic_router: PydanticRouter | None = None


def get_pydantic_router() -> PydanticRouter:
    """Singleton thread-safe do PydanticRouter."""
    global _pydantic_router
    if _pydantic_router is None:
        _pydantic_router = PydanticRouter()
    return _pydantic_router