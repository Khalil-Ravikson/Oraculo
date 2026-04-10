"""
src/domain/services/oraculo_router.py
======================================

CORREÇÃO RISCOS 2 E 3:
  Risco 2: SemanticRouterService.rotear() é síncrono → bloqueava o event loop.
  Risco 3: PydanticRouter.rotear()       é síncrono → await direto gerava TypeError.

SOLUÇÃO: asyncio.to_thread() envolve ambas as chamadas síncronas,
movendo-as para o thread pool do Python sem bloquear o event loop
do FastAPI/LangGraph/Celery.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_NODE_MAP = {
    "CALENDARIO": "rag_node",
    "EDITAL":     "rag_node",
    "CONTATOS":   "rag_node",
    "WIKI":       "rag_node",
    "CRUD":       "crud_node",
    "GREETING":   "greeting_node",
    "GERAL":      "rag_node",
}


class OraculoRouterService:
    """
    Cascata assíncrona em 3 camadas.
    Todos os roteadores síncronos são executados via asyncio.to_thread()
    para não bloquear o event loop — fix definitivo dos Riscos 2 e 3.
    """

    def __init__(self, semantic_router, pydantic_router):
        self._semantic = semantic_router   # SemanticRouterService (sync)
        self._pydantic = pydantic_router   # PydanticRouter        (sync)
        self._knn_threshold = 0.85

    async def rotear(
        self,
        mensagem: str,
        contexto: dict,
        is_admin: bool = False,
    ) -> dict:
        """
        Roteia de forma totalmente assíncrona.
        Retorna dict compatível com OracleState:
          {"route": str, "crag_score": float, "_skip_cache": bool}
        """

        # ── Camada 1: KNN Semântico (sync → thread pool) ──────────────────────
        # FIX Risco 2: asyncio.to_thread evita bloqueio do event loop
        try:
            res = await asyncio.to_thread(
                self._semantic.rotear,
                mensagem,
                is_admin=is_admin,
            )
            if res.score >= self._knn_threshold:
                logger.debug("🚦 KNN: %s (%.3f)", res.route, res.score)
                return {
                    "route":       res.route,
                    "crag_score":  res.score,
                    "_skip_cache": False,
                }
            logger.debug(
                "🤔 KNN confiança baixa (%.3f < %.2f) → Pydantic Router",
                res.score, self._knn_threshold,
            )
        except Exception as e:
            logger.warning("⚠️  KNN Router falhou: %s", e)

        # ── Camada 2: Pydantic/LLM Router (sync → thread pool) ────────────────
        # FIX Risco 3: PydanticRouter.rotear() é sync — o await direto
        # anterior causava TypeError. Agora usamos asyncio.to_thread().
        try:
            res = await asyncio.to_thread(
                self._pydantic.rotear,
                mensagem,
                contexto_usuario=contexto,
            )
            route = _NODE_MAP.get(res.decisao, "rag_node")
            logger.debug("🚦 Pydantic: %s → %s (%.3f)", res.decisao, route, res.confianca)
            return {
                "route":       route,
                "crag_score":  res.confianca,
                "_skip_cache": res.skip_cache,
            }
        except Exception as e:
            logger.error("❌ Pydantic Router falhou: %s", e)

        # ── Fallback seguro ────────────────────────────────────────────────────
        logger.warning("⚠️  Ambos os roteadores falharam — fallback rag_node")
        return {"route": "rag_node", "crag_score": 0.0, "_skip_cache": True}