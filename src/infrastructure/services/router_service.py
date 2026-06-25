"""
src/infrastructure/services/router_service.py
----------------------------------------------
SERVICE PURO de roteamento — sem Celery, sem HTTP.
Lê regex e KNN do Redis (populado pelo IntentSeederService no boot).

PRIORIDADE:
  1. Regex exato  (0ms, 0 tokens) → confiança alta
  2. KNN Redis    (~5ms, 0 tokens) → confiança média
  3. Gemini Flash (~80ms, ~50 tokens) → só se confiança < threshold

CONTRATO:
  RouterService.rotear(query, ctx) → RouterDecision
  Nunca lança exceção — fallback para GERAL em caso de falha.
"""
from __future__ import annotations

import json
import logging
import re
import struct
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

REDIS_REGEX_KEY   = "router:regex"
REDIS_CONFIG_KEY  = "router:config"
PREFIX_TOOLS      = "tools:emb:"
IDX_TOOLS         = "idx:tools"

CONF_CACHE_HIT    = 1.0
CONF_REGEX        = 0.90
CONF_KNN_ALTO     = 0.82
CONF_KNN_BAIXO    = 0.60
CONF_FLASH        = 0.75
CONF_FALLBACK     = 0.30
THRESHOLD_KNN     = 0.72   # similaridade coseno mínima para aceitar KNN
THRESHOLD_FLASH   = 0.55   # confiança mínima para aceitar Flash


@dataclass
class RouterDecision:
    rota: str
    confianca: float
    metodo: str        # "regex" | "knn" | "flash" | "fallback"
    doc_type: str = "geral"
    k_vector: int = 6
    k_text: int = 8
    latencia_ms: int = 0


class RouterService:
    """
    Serviço de roteamento com fallback em cascata.
    Thread-safe e stateless após construção.
    """

    def __init__(self, redis_client: Any = None, embedding_model: Any = None):
        self._r = redis_client
        self._emb = embedding_model
        self._regex_cache: dict[str, re.Pattern] = {}

    # ── API pública ────────────────────────────────────────────────────────────

    async def rotear(
        self,
        query: str,
        user_context: dict | None = None,
    ) -> RouterDecision:
        t0 = time.monotonic()
        r = self._get_redis()

        # 1. Regex (mais rápido, sem custo)
        decision = self._tentar_regex(query, r)
        if decision and decision.confianca >= CONF_REGEX:
            decision.latencia_ms = int((time.monotonic() - t0) * 1000)
            return decision

        # 2. KNN Redis (sem LLM)
        knn_decision = await self._tentar_knn(query, r)
        if knn_decision and knn_decision.confianca >= THRESHOLD_KNN:
            knn_decision.latencia_ms = int((time.monotonic() - t0) * 1000)
            return knn_decision

        # 3. Gemini Flash (último recurso, só para ambíguos)
        best_so_far = knn_decision or decision
        flash_decision = await self._tentar_flash(query, user_context or {})
        if flash_decision and flash_decision.confianca >= THRESHOLD_FLASH:
            flash_decision.latencia_ms = int((time.monotonic() - t0) * 1000)
            return flash_decision

        # 4. Fallback
        fallback = best_so_far or RouterDecision(
            rota="GERAL", confianca=CONF_FALLBACK, metodo="fallback"
        )
        fallback.latencia_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "🧭 [ROUTER] %s (conf=%.2f, método=%s, %dms)",
            fallback.rota, fallback.confianca, fallback.metodo, fallback.latencia_ms,
        )
        return fallback

    # ── Camada 1: Regex ───────────────────────────────────────────────────────

    def _tentar_regex(self, query: str, r: Any) -> RouterDecision | None:
        try:
            # Carrega regex do Redis (hash atualizado pelo seeder)
            todas = r.hgetall(REDIS_REGEX_KEY)
            if not todas:
                return None

            q_lower = query.lower().strip()
            for nome_raw, regex_raw in todas.items():
                nome  = nome_raw if isinstance(nome_raw, str) else nome_raw.decode()
                regex = regex_raw if isinstance(regex_raw, str) else regex_raw.decode()

                if not regex:
                    continue

                # Cache os patterns compilados
                if nome not in self._regex_cache:
                    try:
                        self._regex_cache[nome] = re.compile(regex, re.IGNORECASE)
                    except re.error:
                        continue

                if self._regex_cache[nome].search(q_lower):
                    cfg = self._get_config(r, nome)
                    return RouterDecision(
                        rota=nome,
                        confianca=CONF_REGEX,
                        metodo="regex",
                        **cfg,
                    )
        except Exception as e:
            logger.debug("Router regex falhou: %s", e)
        return None

    # ── Camada 2: KNN Redis ───────────────────────────────────────────────────

    async def _tentar_knn(self, query: str, r: Any) -> RouterDecision | None:
        try:
            import asyncio
            import numpy as np
            from redis.commands.search.query import Query as RQuery

            emb = self._get_embeddings()
            vetor = await asyncio.to_thread(emb.embed_query, query.lower().strip())
            vetor_bytes = np.array(vetor, dtype=np.float32).tobytes()

            q = (
                RQuery("*=>[KNN 1 @embedding $vec AS score]")
                .sort_by("score")
                .return_fields("name", "score")
                .dialect(2)
                .paging(0, 1)
            )
            res = r.ft(IDX_TOOLS).search(q, {"vec": vetor_bytes})

            if not res.docs:
                return None

            doc = res.docs[0]
            similaridade = max(0.0, 1.0 - float(getattr(doc, "score", 1.0)))
            nome = getattr(doc, "name", "GERAL")
            cfg = self._get_config(r, nome)

            confianca = CONF_KNN_ALTO if similaridade >= 0.80 else CONF_KNN_BAIXO
            return RouterDecision(
                rota=nome,
                confianca=confianca * similaridade,
                metodo="knn",
                **cfg,
            )
        except Exception as e:
            logger.debug("Router KNN falhou: %s", e)
        return None

    # ── Camada 3: Gemini Flash ────────────────────────────────────────────────

    async def _tentar_flash(
        self, query: str, ctx: dict
    ) -> RouterDecision | None:
        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            rotas_disponiveis = list(self._get_redis().hkeys(REDIS_CONFIG_KEY))
            rotas_str = ", ".join(
                r if isinstance(r, str) else r.decode()
                for r in rotas_disponiveis
            ) or "CALENDARIO, EDITAL, CONTATOS, WIKI, CRUD, GREETING, GERAL"

            prompt = (
                f'Rotas: {rotas_str}\n'
                f'Contexto: {json.dumps({k: ctx.get(k) for k in ("curso","centro") if ctx.get(k)})}\n'
                f'Mensagem: "{query[:300]}"\n'
                'Classifique. JSON: {"rota": "...", "confianca": 0.0}'
            )

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Você classifica mensagens de alunos universitários da UEMA. "
                        "Responda APENAS com JSON válido sem markdown."
                    ),
                    temperature=0.0,
                    max_output_tokens=60,
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(response.text or "{}")
            rota = str(data.get("rota", "GERAL")).upper()
            confianca = float(data.get("confianca", 0.5))

            cfg = self._get_config(self._get_redis(), rota)
            return RouterDecision(
                rota=rota,
                confianca=confianca,
                metodo="flash",
                **cfg,
            )
        except Exception as e:
            logger.warning("Router Flash falhou: %s", e)
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_config(self, r: Any, nome: str) -> dict:
        """Retorna doc_type, k_vector, k_text para a intent."""
        try:
            raw = r.hget(REDIS_CONFIG_KEY, nome)
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

    def _get_redis(self) -> Any:
        if self._r is None:
            from src.infrastructure.redis_client import get_redis
            self._r = get_redis()
        return self._r

    def _get_embeddings(self) -> Any:
        if self._emb is None:
            from src.rag.embeddings import get_embeddings
            self._emb = get_embeddings()
        return self._emb