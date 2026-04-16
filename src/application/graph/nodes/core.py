"""
src/application/graph/nodes/core.py — v2 (DI correto)
======================================================

CORREÇÕES vs v1:
  - _get_retriever() instanciava RedisVectorAdapter() sem argumentos.
    Agora usa RedisVLVectorAdapter(embeddings) com embeddings injectados.
  - _get_llm() usava import circular — agora usa get_embeddings() lazy.
  - Logs de debug em cada nó (rastreabilidade no terminal Docker).
  - logger.exception() em todos os blocos de erro (sem silêncio).
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

CRAG_THRESHOLD    = 0.30
MAX_REWRITE_LOOPS = 2


# ─── Singletons lazy (evitam import circular no module-level) ─────────────────

@lru_cache(maxsize=1)
def _get_llm():
    """GeminiProvider singleton — carregado uma vez."""
    from src.infrastructure.adapters.gemini_provider import GeminiProvider
    provider = GeminiProvider()
    logger.debug("✅ [CORE] GeminiProvider inicializado.")
    return provider


@lru_cache(maxsize=1)
def _get_retriever():
    """
    RetrieveContextUseCase com RedisVLVectorAdapter.

    CORREÇÃO: RedisVLVectorAdapter exige embeddings_model no construtor.
    Antes: RedisVectorAdapter() sem argumentos → AttributeError em runtime.
    Agora: RedisVLVectorAdapter(embeddings) → correto.
    """
    from src.rag.embeddings import get_embeddings
    from src.infrastructure.adapters.redis_vector_adapter import RedisVLVectorAdapter
    from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase

    embeddings = get_embeddings()
    adapter    = RedisVLVectorAdapter(embeddings_model=embeddings)
    retriever  = RetrieveContextUseCase(vector_store=adapter)
    logger.debug("✅ [CORE] RetrieveContextUseCase (RedisVL) inicializado.")
    return retriever


def _system_prompt() -> str:
    """Lê system prompt do Redis (admin pode sobrescrever em runtime)."""
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
    Nós do Agentic RAG agrupados em classe.
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

        logger.debug(
            "🔀 [NODE:CLASSIFY] msg='%.60s' | is_admin=%s",
            msg, is_admin,
        )

        # Retomada HITL
        pending = state.get("pending_confirmation")
        if pending and state.get("confirmation_result") not in (
            "confirmed", "cancelled", "awaiting_token"
        ):
            lower = msg.lower().strip()
            if lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
                logger.debug("✅ [NODE:CLASSIFY] HITL confirmado")
                return {"confirmation_result": "confirmed"}
            if lower in ("não", "nao", "n", "no", "cancelar"):
                logger.debug("⛔ [NODE:CLASSIFY] HITL cancelado")
                return {
                    "confirmation_result": "cancelled",
                    "final_response":      "❌ Operação cancelada.",
                    "route":               "respond_only",
                }
            return {
                "final_response": f"{pending}\n\nResponda *SIM* ou *NÃO*.",
                "route":          "respond_only",
            }

        # Modo manutenção (admin bypassa)
        try:
            from src.infrastructure.redis_client import get_redis_text
            flag = get_redis_text().get("admin:maintenance_mode")
            if isinstance(flag, bytes):
                flag = flag.decode()
            if flag == "1" and not is_admin:
                logger.info("🔧 [NODE:CLASSIFY] Modo manutenção activo.")
                return {
                    "final_response": "🔧 *Oráculo em manutenção.* Voltarei em breve!",
                    "route":          "respond_only",
                }
        except Exception:
            pass

        # Roteamento principal
        contexto = {
            "curso":   state.get("curso"),
            "periodo": state.get("periodo"),
            "centro":  state.get("centro"),
        }

        try:
            resultado = await self._router.rotear(msg, contexto, is_admin)
        except Exception as exc:
            logger.exception(
                "❌ [NODE:CLASSIFY] Router falhou | causa=%s | msg='%.60s': %s",
                type(exc).__name__, msg[:60], exc,
            )
            resultado = {"route": "retrieve_node", "crag_score": 0.0}

        ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "✅ [NODE:CLASSIFY] route='%s' | %dms",
            resultado.get("route"), ms,
        )
        return resultado

    # ── Nó 2: Retrieve ────────────────────────────────────────────────────────

    async def node_retrieve(self, state: "OracleState") -> dict:
        """Busca documentos no Redis. Não gera resposta."""
        t0    = time.monotonic()
        msgs  = state.get("messages", [])
        query = msgs[-1].content if msgs else state.get("current_input", "")
        route = state.get("route", "geral").upper()

        logger.debug(
            "🔍 [NODE:RETRIEVE] route=%s | query='%.60s'",
            route, query,
        )

        rag_context = ""
        crag_score  = 0.0

        try:
            from src.rag.query.transformer import QueryTransformer
            from src.rag.query.protocols import RawQuery

            raw         = RawQuery(text=query, fatos_usuario=[])
            transformer = QueryTransformer.build_for_route(route)
            qt          = transformer.transform(raw)

            retriever = _get_retriever()
            resultado  = await retriever.executar(qt)

            if resultado.encontrou:
                rag_context = resultado.contexto_formatado
                scores = [c.rrf_score for c in resultado.chunks if c.rrf_score > 0]
                crag_score = sum(scores) / len(scores) if scores else 0.0

            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "✅ [NODE:RETRIEVE] chunks=%d | crag=%.3f | %dms",
                len(resultado.chunks) if resultado.encontrou else 0,
                crag_score, ms,
            )

        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "❌ [NODE:RETRIEVE] Falha | causa=%s | %dms | erro: %s",
                type(exc).__name__, ms, exc,
            )

        return {"rag_context": rag_context, "crag_score": crag_score}

    # ── Nó 3: Grade Documents (CRAG) ──────────────────────────────────────────

    async def node_grade_documents(self, state: "OracleState") -> dict:
        """CRAG: avalia qualidade do retrieval."""
        score      = state.get("crag_score", 0.0)
        loop_count = state.get("loop_count", 0)

        if score < CRAG_THRESHOLD and loop_count < MAX_REWRITE_LOOPS:
            logger.info(
                "📉 [NODE:GRADE] Score baixo (%.3f < %.2f) → rewrite (loop %d/%d)",
                score, CRAG_THRESHOLD, loop_count + 1, MAX_REWRITE_LOOPS,
            )
            return {"relevance": "no"}

        logger.debug(
            "✅ [NODE:GRADE] Score aceite (%.3f) | loop=%d",
            score, loop_count,
        )
        return {"relevance": "yes"}

    # ── Nó 4: Rewrite Query ────────────────────────────────────────────────────

    async def node_rewrite_query(self, state: "OracleState") -> dict:
        """Self-RAG: pede ao LLM uma versão melhorada da query."""
        t0   = time.monotonic()
        msgs = state.get("messages", [])
        query_orig = msgs[-1].content if msgs else state.get("current_input", "")

        logger.debug(
            "🔄 [NODE:REWRITE] query_orig='%.60s'", query_orig
        )

        prompt = (
            f"A busca pela pergunta abaixo não encontrou documentos relevantes.\n"
            f"Reescreva de forma mais técnica e específica para busca académica UEMA.\n"
            f"Responda APENAS com a pergunta reescrita, sem explicações.\n\n"
            f"Pergunta original: {query_orig}"
        )

        nova_query = query_orig   # fallback conservador
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
                query_orig[:40], nova_query[:40], ms,
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

        logger.debug(
            "✍️  [NODE:GENERATE] query='%.60s' | ctx_len=%d",
            query, len(rag_context),
        )

        from src.application.graph.prompts import montar_prompt_geracao

        perfil = ""
        if state.get("user_name") or state.get("curso"):
            perfil = f"Aluno: {state.get('user_name', '')} | Curso: {state.get('curso', '')}"

        prompt = montar_prompt_geracao(
            pergunta     = query,
            contexto_rag = rag_context,
            perfil_usuario = perfil,
        )

        resposta_fallback = (
            "Não encontrei informações específicas sobre isso nos documentos. "
            "Posso tentar ajudar com outra dúvida relacionada à UEMA?"
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
                    "messages":      [AIMessage(content=resp.conteudo)],
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
            "messages":      [AIMessage(content=resposta_fallback)],
            "final_response": resposta_fallback,
        }