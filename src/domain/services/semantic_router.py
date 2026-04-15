"""
src/domain/services/semantic_router.py
=======================================
SemanticRouterService totalmente assíncrono.

ANTES (bug crítico):
    asyncio.to_thread(self._semantic.rotear, ...) → cria thread por request.
    Com 50 usuários simultâneos = 50 threads bloqueadas no pool do OS.

DEPOIS (correto):
    await r.ft(IDX_TOOLS).search(query, params) → I/O não-bloqueante nativo.
    Com 50 usuários simultâneos = 50 coroutines no mesmo event loop. Zero threads extras.

DIMENSÃO VETORIAL:
    VECTOR_DIM = 3072 (gemini-embedding-001, modelos/gemini-3.1-flash-lite).
    O índice idx:tools e idx:rag:chunks DEVEM ter a mesma dimensão.
    Inconsistência aqui → silenciosamente retorna resultados errados (score ~0.5 aleatório).
"""
from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis
from redis.commands.search.query import Query as RQuery

logger = logging.getLogger(__name__)

# ─── Mapeamento intent → nó do LangGraph ─────────────────────────────────────
_INTENT_TO_NODE: dict[str, str] = {
    # Intents registadas no Redis (idx:tools)
    "intent_greeting":              "greeting_node",
    "intent_crud":                  "crud_node",
    "intent_admin":                 "admin_command_node",
    # RAG tools — todas convergem para retrieve_node
    "consultar_calendario_academico": "retrieve_node",
    "consultar_edital_paes_2026":     "retrieve_node",
    "consultar_contatos_uema":        "retrieve_node",
    "consultar_wiki_ctic":            "retrieve_node",
    "abrir_chamado_glpi":             "crud_node",     # tool sensível → HITL
}

# Thresholds calibrados empiricamente no corpus UEMA
_THRESHOLD_HIGH   = 0.82   # confiança alta → sem LLM fallback
_THRESHOLD_MEDIUM = 0.60   # confiança média → vai para PydanticRouter


@dataclass(frozen=True)
class SemanticRouteResult:
    """Resultado imutável — serializa limpo para o OracleState."""
    node:      str
    intent:    str
    score:     float
    confianca: str   # "alta" | "media" | "baixa"
    latency_ms: int


class SemanticRouterService:
    """
    Roteador semântico 100% assíncrono via Redis Stack.

    Recebe injeção do cliente async (não cria conexão internamente).
    Isso permite:
      - Reutilização do pool de conexões existente do FastAPI.
      - Mock trivial em testes unitários.
      - Substituição por outro backend (Pinecone, Weaviate) sem tocar no orquestrador.
    """

    def __init__(
        self,
        async_redis: aioredis.Redis,
        embeddings_model,
        idx_tools: str = "idx:tools",
        threshold_high: float = _THRESHOLD_HIGH,
        threshold_medium: float = _THRESHOLD_MEDIUM,
    ) -> None:
        self._r               = async_redis
        self._emb             = embeddings_model
        self._idx             = idx_tools
        self._threshold_high  = threshold_high
        self._threshold_med   = threshold_medium

    # ── Atalhos de regex (0ms, 0 tokens) ─────────────────────────────────────

    def _fast_path(self, texto: str) -> Optional[SemanticRouteResult]:
        """
        Short-circuit para padrões óbvios antes de qualquer I/O.
        Regex no event loop é válido — é CPU-bound mas < 0.1ms.
        """
        import re
        t = texto.lower().strip()

        saudacoes = re.compile(
            r"^(oi|olá|ola|bom dia|boa tarde|boa noite|tudo bem|e aí|hey|hi)\s*[!.]?$"
        )
        if saudacoes.match(t):
            return SemanticRouteResult(
                node="greeting_node", intent="intent_greeting",
                score=1.0, confianca="alta", latency_ms=0,
            )
        return None

    # ── Busca vetorial assíncrona ─────────────────────────────────────────────

    async def rotear(
        self,
        texto: str,
        is_admin: bool = False,
    ) -> SemanticRouteResult:
        """
        Ponto de entrada público. Totalmente não-bloqueante.

        FLUXO:
          1. Fast-path regex (0ms)
          2. Gera embedding do texto (CPU-bound via asyncio.to_thread — ÚNICA exceção
             justificada: o modelo de embedding é síncrono e é o gargalo real de CPU,
             não de I/O. Isolamos aqui para não bloquear o event loop.)
          3. Busca KNN assíncrona no Redis (I/O puro, não-bloqueante)
          4. Mapeia intent → nó do LangGraph
        """
        t0 = time.monotonic()

        # ── 1. Fast-path ──────────────────────────────────────────────────────
        fast = self._fast_path(texto)
        if fast:
            logger.debug(
                "⚡ [ROUTER] Fast-path: '%s' → %s (0ms)",
                texto[:50], fast.node,
            )
            return fast

        # ── 2. Embedding (CPU-bound isolado) ──────────────────────────────────
        import asyncio
        try:
            vetor: list[float] = await asyncio.to_thread(
                self._emb.embed_query, texto
            )
        except Exception as exc:
            logger.exception(
                "❌ [ROUTER] Falha ao gerar embedding para roteamento | texto='%s' | erro: %s",
                texto[:80], exc,
            )
            return self._fallback_result(texto, int((time.monotonic() - t0) * 1000))

        # ── 3. KNN assíncrono no Redis ────────────────────────────────────────
        embedding_bytes = struct.pack(f"{len(vetor)}f", *vetor)
        query = (
            RQuery("*=>[KNN 3 @embedding $vec AS knn_score]")
            .sort_by("knn_score")
            .return_fields("name", "knn_score")
            .dialect(2)
            .paging(0, 3)
        )

        try:
            results = await self._r.ft(self._idx).search(
                query, query_params={"vec": embedding_bytes}
            )
        except Exception as exc:
            # Detalha o erro: Connection Refused vs Timeout vs Index Missing
            cause = type(exc).__name__
            logger.exception(
                "❌ [ROUTER] Redis KNN search falhou | idx=%s | causa=%s | detalhe: %s",
                self._idx, cause, exc,
            )
            return self._fallback_result(texto, int((time.monotonic() - t0) * 1000))

        latency_ms = int((time.monotonic() - t0) * 1000)

        if not results.docs:
            logger.warning(
                "⚠️  [ROUTER] idx:tools vazio — nenhuma intent registada | "
                "Execute o seed do router antes de subir o bot.",
            )
            return self._fallback_result(texto, latency_ms)

        # ── 4. Extrai melhor match ────────────────────────────────────────────
        top         = results.docs[0]
        intent_name = getattr(top, "name", "")
        # Redis retorna distância coseno (0=idêntico, 2=oposto). Convertemos para similaridade.
        distance    = float(getattr(top, "knn_score", 1.0))
        similarity  = max(0.0, 1.0 - distance)

        confianca = (
            "alta"  if similarity >= self._threshold_high else
            "media" if similarity >= self._threshold_med  else
            "baixa"
        )

        # Admin bypass: se a intent é admin mas o usuário não é admin,
        # degrada para retrieve_node (não silencia — loga para auditoria).
        node = _INTENT_TO_NODE.get(intent_name, "retrieve_node")
        if node == "admin_command_node" and not is_admin:
            logger.warning(
                "🚫 [ROUTER] Intent 'intent_admin' detectada para não-admin | "
                "texto='%s' | score=%.4f | degradando para retrieve_node",
                texto[:60], similarity,
            )
            node = "retrieve_node"

        logger.info(
            "🎯 [ROUTER] KNN | intent='%s' → node='%s' | "
            "score=%.4f | confiança=%s | latência=%dms",
            intent_name, node, similarity, confianca, latency_ms,
        )

        return SemanticRouteResult(
            node=node, intent=intent_name,
            score=similarity, confianca=confianca, latency_ms=latency_ms,
        )

    # ── Fallback de emergência ────────────────────────────────────────────────

    def _fallback_result(self, texto: str, latency_ms: int) -> SemanticRouteResult:
        """
        Retornado quando Redis ou embedding falham.
        Nunca deixa o sistema morrer — degrada graciosamente para RAG genérico.
        """
        # Mini-regex de último recurso (sem I/O)
        t = texto.lower()
        if any(k in t for k in ("paes", "vaga", "edital", "vestibular", "inscri")):
            node, intent = "retrieve_node", "consultar_edital_paes_2026"
        elif any(k in t for k in ("matrícula", "matricula", "calendário", "calendario", "prazo")):
            node, intent = "retrieve_node", "consultar_calendario_academico"
        elif any(k in t for k in ("email", "telefone", "contato", "ctic")):
            node, intent = "retrieve_node", "consultar_contatos_uema"
        else:
            node, intent = "retrieve_node", "geral"

        logger.warning(
            "⚠️  [ROUTER] Fallback regex ativado | node='%s' | latência=%dms",
            node, latency_ms,
        )
        return SemanticRouteResult(
            node=node, intent=intent,
            score=0.0, confianca="baixa", latency_ms=latency_ms,
        )