"""
src/application/graph/nodes/core.py — v3 CORRIGIDO
====================================================

CORREÇÕES APLICADAS:
  1. node_grade_documents: PAROU de ler crag_score do roteador.
     Agora calcula a relevância REAL dos chunks vindos do Redis.
     Antes: score era sempre 0.0 porque o retrieve_node não regravia crag_score.
     Depois: se há chunks e o RRF score médio > THRESHOLD_DOC → gera.
             se RRF score baixo → rewrite_query (Self-RAG).

  2. retrieve_node: logs de debug dos chunks (source, score, preview).
     Visíveis com LOG_LEVEL=DEBUG; sem overhead em produção.

  3. _get_retriever: agora instancia RedisVLVectorAdapter(embeddings) corretamente.
     Antes criava sem argumento → AttributeError silencioso em runtime.

  4. Logging configurado com getLogger(__name__) em nível INFO.
     O root logger já está configurado no startup (main.py / _startup).
     Se os logs não aparecem → verificar LOG_LEVEL no .env.

  5. CRAG_THRESHOLD ajustado para 0.012 (escala RRF, não coseno).
     RRF score = Σ 1/(k+rank). Com k=60 e top-1 → 1/61 ≈ 0.016.
     Score > 0.012 = ao menos um chunk no top-3 das duas buscas.
     Score < 0.012 = nada relevante encontrado → rewrite.

  6. node_rewrite_query: incrementa loop_count e passa mensagem reescrita
     de volta para o retrieve_node via state["messages"].
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage

if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

# ─── Limiares ────────────────────────────────────────────────────────────────
# CORRIGIDO: escala RRF, não coseno.
# RRF score = Σ 1/(60 + rank).  Top-1 em ambas as buscas ≈ 0.032.
# Qualquer chunk relevante no top-5 → score ≥ 0.013.
CRAG_THRESHOLD    = 0.012
MAX_REWRITE_LOOPS = 2


# ─── Singletons lazy ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_llm():
    """GeminiProvider singleton."""
    from src.infrastructure.adapters.gemini_provider import GeminiProvider
    provider = GeminiProvider()
    logger.debug("✅ [CORE] GeminiProvider inicializado.")
    return provider


@lru_cache(maxsize=1)
def _get_retriever():
    """
    RetrieveContextUseCase com RedisVLVectorAdapter.

    CORRIGIDO: RedisVLVectorAdapter EXIGE embeddings_model no construtor.
    Antes: RedisVLVectorAdapter() → AttributeError silencioso em runtime.
    Agora: RedisVLVectorAdapter(embeddings_model=embeddings) → correto.
    """
    from src.rag.embeddings import get_embeddings
    from src.infrastructure.adapters.redis_vector_adapter import RedisVLVectorAdapter
    from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase

    embeddings = get_embeddings()
    adapter    = RedisVLVectorAdapter(embeddings_model=embeddings)
    retriever  = RetrieveContextUseCase(vector_store=adapter)
    logger.info("✅ [CORE] RetrieveContextUseCase (RedisVL) inicializado.")
    return retriever


def _system_prompt() -> str:
    """Lê system prompt customizado do Redis (admin pode sobrescrever em runtime)."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.application.graph.prompts import SYSTEM_UEMA
        val = get_redis_text().get("admin:system_prompt")
        if val:
            return val.decode() if isinstance(val, bytes) else val
        return SYSTEM_UEMA
    except Exception:
        from src.application.graph.prompts import SYSTEM_UEMA
        return SYSTEM_UEMA


# ─────────────────────────────────────────────────────────────────────────────
# Nós do Agentic RAG
# ─────────────────────────────────────────────────────────────────────────────

class OraculoCoreNodes:
    """
    Nós do Agentic RAG.
    Recebe o router por injeção — permite mock em testes.
    """

    def __init__(self, oraculo_router) -> None:
        self._router = oraculo_router

    # ── Nó 1: Classify ────────────────────────────────────────────────────────

    async def node_classify(self, state: "OracleState") -> dict:
        """Porteiro do grafo — decide a rota sem LLM sempre que possível."""
        t0   = time.monotonic()
        msgs = state.get("messages", [])
        msg  = msgs[-1].content if msgs else state.get("current_input", "")
        is_admin = state.get("is_admin", False)

        logger.info(
            "🔀 [NODE:CLASSIFY] msg='%.80s' | is_admin=%s",
            msg, is_admin,
        )

        # ── Retomada HITL ─────────────────────────────────────────────────────
        pending = state.get("pending_confirmation")
        if pending and state.get("confirmation_result") not in (
            "confirmed", "cancelled", "awaiting_token"
        ):
            lower = msg.lower().strip()
            if lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
                logger.info("✅ [NODE:CLASSIFY] HITL confirmado")
                return {"confirmation_result": "confirmed"}
            if lower in ("não", "nao", "n", "no", "cancelar"):
                logger.info("⛔ [NODE:CLASSIFY] HITL cancelado")
                return {
                    "confirmation_result": "cancelled",
                    "final_response":      "❌ Operação cancelada.",
                    "route":               "respond_only",
                }
            return {
                "final_response": f"{pending}\n\nResponda *SIM* ou *NÃO*.",
                "route":          "respond_only",
            }

        # ── Modo manutenção ───────────────────────────────────────────────────
        try:
            from src.infrastructure.redis_client import get_redis_text
            flag = get_redis_text().get("admin:maintenance_mode")
            if isinstance(flag, bytes):
                flag = flag.decode()
            if flag == "1" and not is_admin:
                return {
                    "final_response": "🔧 *Oráculo em manutenção.* Voltarei em breve!",
                    "route":          "respond_only",
                }
        except Exception:
            pass

        # ── Roteamento principal ──────────────────────────────────────────────
        contexto = {
            "curso":   state.get("curso"),
            "periodo": state.get("periodo"),
            "centro":  state.get("centro"),
        }

        try:
            resultado = await self._router.rotear(msg, contexto, is_admin)
        except Exception as exc:
            logger.exception(
                "❌ [NODE:CLASSIFY] Router falhou | causa=%s: %s",
                type(exc).__name__, exc,
            )
            resultado = {"route": "retrieve_node", "crag_score": 0.0}

        ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "✅ [NODE:CLASSIFY] route='%s' | score=%.3f | %dms",
            resultado.get("route"), resultado.get("crag_score", 0.0), ms,
        )
        return resultado

    # ── Nó 2: Retrieve ────────────────────────────────────────────────────────

    async def node_retrieve(self, state: "OracleState") -> dict:
        """
        Busca documentos no Redis.
        NOVO: loga cada chunk retornado (source, score, preview).
        NÃO grava crag_score — quem faz isso é node_grade_documents.
        """
        t0    = time.monotonic()
        msgs  = state.get("messages", [])
        query = msgs[-1].content if msgs else state.get("current_input", "")
        route = state.get("route", "geral").upper()

        logger.info(
            "🔍 [NODE:RETRIEVE] route=%s | query='%.80s'",
            route, query,
        )

        rag_context = ""
        chunks_raw: list[dict] = []

        try:
            from src.rag.query.transformer import QueryTransformer
            from src.rag.query.protocols import RawQuery

            raw         = RawQuery(text=query, fatos_usuario=[])
            transformer = QueryTransformer.build_for_route(route)
            qt          = transformer.transform(raw)

            retriever  = _get_retriever()
            resultado  = await retriever.executar(qt)

            if resultado.encontrou:
                rag_context = resultado.contexto_formatado
                chunks_raw  = [
                    {
                        "source":    c.source,
                        "rrf_score": c.rrf_score,
                        "preview":   c.content[:80].replace("\n", " "),
                    }
                    for c in resultado.chunks
                ]

            ms = int((time.monotonic() - t0) * 1000)

            # ── DEBUG: lista os chunks para rastreio ──────────────────────────
            if chunks_raw:
                logger.info(
                    "📦 [NODE:RETRIEVE] %d chunks encontrados | %dms",
                    len(chunks_raw), ms,
                )
                for i, c in enumerate(chunks_raw, 1):
                    logger.debug(
                        "  chunk %d | source=%s | rrf=%.4f | preview='%s'",
                        i, c["source"], c["rrf_score"], c["preview"],
                    )
            else:
                logger.warning(
                    "⚠️  [NODE:RETRIEVE] 0 chunks para query='%.60s' | %dms",
                    query, ms,
                )

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:RETRIEVE] Falha | causa=%s | %dms: %s",
                type(exc).__name__, ms, exc,
            )

        return {
            "rag_context":  rag_context,
            "chunks_debug": chunks_raw,   # passa para grade_documents avaliar
        }

    # ── Nó 3: Grade Documents (CRAG) ──────────────────────────────────────────

    async def node_grade_documents(self, state: "OracleState") -> dict:
        """
        CRAG: avalia qualidade REAL dos chunks recuperados.

        CORRIGIDO: não lê mais crag_score do roteador.
        Agora calcula o score médio dos RRF scores dos chunks do Redis.

        Lógica:
          - Se não há chunks                → score = 0.0 → rewrite
          - Se score médio < CRAG_THRESHOLD → rewrite (Self-RAG)
          - Se score médio ≥ CRAG_THRESHOLD → gera resposta

        Escala RRF:
          Chunk no top-1 de ambas buscas: 1/61 + 1/61 ≈ 0.032
          Chunk apenas na busca vetorial:  1/61 ≈ 0.016
          Chunk irrelevante (rank > 8):    < 0.012
        """
        t0         = time.monotonic()
        loop_count = state.get("loop_count", 0)
        chunks     = state.get("chunks_debug", [])

        # ── Calcula score real ────────────────────────────────────────────────
        if not chunks:
            score = 0.0
            logger.warning(
                "📉 [NODE:GRADE] 0 chunks → score=0.0 | loop=%d/%d",
                loop_count, MAX_REWRITE_LOOPS,
            )
        else:
            scores = [c.get("rrf_score", 0.0) for c in chunks if c.get("rrf_score")]
            score  = sum(scores) / len(scores) if scores else 0.0
            logger.info(
                "📊 [NODE:GRADE] chunks=%d | score_medio=%.4f | threshold=%.4f | loop=%d/%d",
                len(chunks), score, CRAG_THRESHOLD, loop_count, MAX_REWRITE_LOOPS,
            )
            # Debug: top-3 chunks
            for i, c in enumerate(chunks[:3], 1):
                logger.debug(
                    "  top%d | source=%s | rrf=%.4f | preview='%s'",
                    i, c.get("source", "?"), c.get("rrf_score", 0), c.get("preview", "")[:60],
                )

        ms = int((time.monotonic() - t0) * 1000)

        # ── Decisão CRAG ──────────────────────────────────────────────────────
        if score < CRAG_THRESHOLD and loop_count < MAX_REWRITE_LOOPS:
            logger.info(
                "🔄 [NODE:GRADE] Score baixo (%.4f < %.4f) → rewrite_query",
                score, CRAG_THRESHOLD,
            )
            return {
                "crag_score": score,
                "relevance":  "no",
            }

        if score < CRAG_THRESHOLD and loop_count >= MAX_REWRITE_LOOPS:
            logger.warning(
                "⚠️  [NODE:GRADE] Loops esgotados, gerando com contexto limitado",
            )

        logger.info(
            "✅ [NODE:GRADE] Score aceite (%.4f) → generate_node | %dms",
            score, ms,
        )
        return {
            "crag_score": score,
            "relevance":  "yes",
        }

    # ── Nó 4: Rewrite Query ────────────────────────────────────────────────────

    async def node_rewrite_query(self, state: "OracleState") -> dict:
        """Self-RAG: pede ao LLM uma versão melhorada da query."""
        t0   = time.monotonic()
        msgs = state.get("messages", [])
        query_orig = msgs[-1].content if msgs else state.get("current_input", "")

        logger.info(
            "🔄 [NODE:REWRITE] query_orig='%.80s' | loop=%d",
            query_orig, state.get("loop_count", 0),
        )

        prompt = (
            f"A busca pela pergunta abaixo não encontrou documentos relevantes "
            f"nos arquivos da UEMA (Calendário Acadêmico, Edital PAES, Contatos, Wiki CTIC).\n"
            f"Reescreva de forma mais técnica e específica, usando termos da área "
            f"acadêmica ou de TI da UEMA conforme o contexto.\n"
            f"Responda APENAS com a pergunta reescrita, sem explicações.\n\n"
            f"Pergunta original: {query_orig}"
        )

        nova_query = query_orig
        try:
            llm  = _get_llm()
            resp = await llm.gerar_resposta_async(
                prompt      = prompt,
                temperatura = 0.1,
                max_tokens  = 150,
            )
            if resp.sucesso and resp.conteudo.strip():
                nova_query = resp.conteudo.strip()

            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "✅ [NODE:REWRITE] '%s' → '%s' | %dms",
                query_orig[:50], nova_query[:50], ms,
            )
        except Exception as exc:
            logger.exception(
                "❌ [NODE:REWRITE] LLM falhou | causa=%s: %s",
                type(exc).__name__, exc,
            )

        return {
            "messages":   [HumanMessage(content=nova_query)],
            "loop_count": state.get("loop_count", 0) + 1,
        }

    # ── Nó 5: Generate ────────────────────────────────────────────────────────

    async def node_generate(self, state: "OracleState") -> dict:
        """Escritor final — monta prompt com contexto RAG e gera resposta."""
        t0   = time.monotonic()
        msgs = state.get("messages", [])
        query       = msgs[-1].content if msgs else state.get("current_input", "")
        rag_context = state.get("rag_context", "")
        crag_score  = state.get("crag_score", 0.0)

        logger.info(
            "✍️  [NODE:GENERATE] query='%.80s' | ctx_chars=%d | crag=%.4f",
            query, len(rag_context), crag_score,
        )

        from src.application.graph.prompts import montar_prompt_geracao

        perfil = ""
        if state.get("user_name") or state.get("curso"):
            perfil = (
                f"Aluno: {state.get('user_name', '')} | "
                f"Curso: {state.get('curso', '')} | "
                f"Centro: {state.get('centro', '')}"
            ).strip(" |")

        prompt = montar_prompt_geracao(
            pergunta       = query,
            contexto_rag   = rag_context,
            perfil_usuario = perfil,
        )

        resposta_fallback = (
            "Não encontrei informações específicas sobre isso nos documentos da UEMA. "
            "Tente reformular a pergunta ou consulte diretamente o site *uema.br*."
        )

        try:
            llm  = _get_llm()
            resp = await llm.gerar_resposta_async(
                prompt             = prompt,
                system_instruction = _system_prompt(),
                temperatura        = 0.2,
            )
            ms = int((time.monotonic() - t0) * 1000)

            if resp.sucesso and resp.conteudo:
                logger.info(
                    "✅ [NODE:GENERATE] %d tokens | %dms",
                    resp.tokens_total, ms,
                )
                return {
                    "messages":       [AIMessage(content=resp.conteudo)],
                    "final_response": resp.conteudo,
                }

            logger.warning(
                "⚠️  [NODE:GENERATE] LLM retornou vazio | sucesso=%s | %dms",
                resp.sucesso, ms,
            )

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:GENERATE] LLM falhou | causa=%s | %dms: %s",
                type(exc).__name__, ms, exc,
            )

        return {
            "messages":       [AIMessage(content=resposta_fallback)],
            "final_response": resposta_fallback,
        }