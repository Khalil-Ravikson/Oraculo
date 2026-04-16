"""
src/domain/services/oraculo_router.py — v6 (100% Async + LangGraph State Match)
================================================================================
Orquestrador do pipeline de roteamento — Clean Architecture.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class RoutingDecision(BaseModel):
    decisao:       str
    confianca:     float
    motivo:        str
    intencao_crud: bool
    skip_cache:    bool = False

@dataclass
class RouterResult:
    route:      str
    confianca:  float
    metodo:     str
    motivo:     str
    score:      float = 0.0
    skip_cache: bool  = False

_NODE_MAP = {
    "CALENDARIO": "retrieve_node",
    "EDITAL":     "retrieve_node",
    "CONTATOS":   "retrieve_node",
    "WIKI":       "retrieve_node",
    "CRUD":       "crud_node",
    "GREETING":   "greeting_node",
    "GERAL":      "retrieve_node",
}

class OraculoRouterService:
    """
    Orquestrador do pipeline de roteamento em 2 camadas.
    """
    def __init__(self, semantic_router: Any, pydantic_router: Any) -> None:
        self._semantic       = semantic_router
        self._pydantic       = pydantic_router
        self._knn_threshold  = 0.85

    async def route_message(self, state: dict) -> dict:
        """
        Roteia de forma 100% assíncrona.
        Lê a mensagem do state e retorna um dict estritamente compatível com OracleState.
        """
        mensagem = state["messages"][-1].content
        contexto = state.get("rag_context", {})
        is_admin = False 

        logger.debug("🗺️  [ROUTER] Iniciando análise da mensagem...")

        # ── Camada 1: SemanticRouter (async nativo) ───────────────────────────
        try:
            res = await self._semantic.rotear(mensagem, is_admin=is_admin)
            score = getattr(res, "score", 0.0)
            route_node = getattr(res, "node", "retrieve_node")

            if score >= self._knn_threshold:
                logger.info("🚦 [ROUTER] Camada 1 (Semantic/KNN) Match: %s (%.3f)", route_node, score)
                return {
                    "route": route_node,
                    "tool_name": None,
                    "_router_meta": {
                        "method": "semantic_router",
                        "score": score,
                        "skip_cache": False
                    }
                }

            logger.debug(
                "🤔 [ROUTER] Camada 1 (Semantic/KNN) Confiança baixa (%.3f < %.2f) → Acionando PydanticRouter",
                score, self._knn_threshold,
            )
        except Exception as exc:
            logger.warning("⚠️  [ROUTER] SemanticRouter falhou (Pulando para LLM): %s", exc)

        # ── Camada 2: PydanticRouter (async via Gemini) ───────────────────────
        import asyncio
        try:
            res = await asyncio.to_thread(
                self._pydantic.rotear,
                mensagem,
                contexto_usuario=contexto,
            )
            
            decisao_llm = getattr(res, "decisao", "GERAL")
            route_node = _NODE_MAP.get(decisao_llm, "retrieve_node")
            confianca_llm = getattr(res, "confianca", 0.5)
            
            logger.info("🚦 [ROUTER] Camada 2 (Pydantic/LLM) Decisão: %s → Nó: %s (Confiança: %.3f)", decisao_llm, route_node, confianca_llm)

            return {
                "route": route_node,
                "tool_name": None,
                "_router_meta": {
                    "method": "pydantic_llm",
                    "intent": decisao_llm,
                    "score": confianca_llm,
                    "skip_cache": getattr(res, "skip_cache", confianca_llm < 0.80),
                }
            }
        except Exception as exc:
            logger.error("❌ [ROUTER] PydanticRouter falhou tragicamente: %s", exc)

        # ── Fallback Seguro (Camada 3) ─────────────────────────────────────────
        logger.warning("⚠️  [ROUTER] Todas as camadas falharam → Direcionando para retrieve_node (RAG)")
        return {
            "route": "retrieve_node", 
            "tool_name": None,
            "_router_meta": {
                "method": "fallback",
                "skip_cache": True
            }
        }