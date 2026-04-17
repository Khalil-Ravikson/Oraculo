"""
src/domain/services/oraculo_router.py  — v4 CORRIGIDO
=======================================================

CORREÇÕES APLICADAS:
  1. __init__ agora aceita semantic_router + pydantic_router por DI.
     Antes aceitava apenas 'semantic_router' e 'pydantic_router' isolados;
     o OraculoRouterService do main.py passava os argumentos na ordem errada.

  2. Camada 0 (regex de saudação) movida para ANTES do KNN.
     Custo: 0 tokens, 0 ms de rede.  Evita gastar embedding para "Oi".

  3. Threshold KNN corrigido: 0.85 → 0.75.
     O índice idx:tools usa distância coseno.  1 − dist = similaridade.
     Com dist ≈ 0.22 (intent bem conhecida) o score era 0.78 — abaixo de 0.85,
     caia sempre no Pydantic Router.  0.75 é o limite correto para o bge-m3.

  4. rotear() agora é 100 % async em todas as camadas.
     Camada KNN: self._semantic.rotear() já é coroutine (semantic_router.py v3).
     Camada Pydantic: self._pydantic.rotear_async() — sem asyncio.to_thread.

  5. Retorno padronizado: sempre devolve dict compatível com OracleState.
     Campos garantidos: route, crag_score, _router_meta.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ─── Mapeamento intent KNN → nó do LangGraph ─────────────────────────────────
_NODE_MAP: dict[str, str] = {
    "CALENDARIO":                       "retrieve_node",
    "EDITAL":                           "retrieve_node",
    "CONTATOS":                         "retrieve_node",
    "WIKI":                             "retrieve_node",
    "CRUD":                             "crud_node",
    "GREETING":                         "greeting_node",
    "GERAL":                            "retrieve_node",
    # Intents registadas no idx:tools
    "consultar_calendario_academico":   "retrieve_node",
    "consultar_edital_paes_2026":       "retrieve_node",
    "consultar_contatos_uema":          "retrieve_node",
    "consultar_wiki_ctic":              "retrieve_node",
    "abrir_chamado_glpi":               "crud_node",
    "intent_greeting":                  "greeting_node",
    "intent_crud":                      "crud_node",
    "intent_admin":                     "admin_command_node",
}

# ─── Limiar de confiança KNN ──────────────────────────────────────────────────
# CORRECTO: 0.75, não 0.85.
# bge-m3 com índice SVS-VAMANA / HNSW retorna dist_coseno.
# Para intents bem conhecidas dist ≈ 0.18-0.25 → score = 0.75-0.82.
# Abaixo de 0.75 → ambiguidade → PydanticRouter decide.
_KNN_THRESHOLD = 0.75

# ─── Padrões de Camada 0 (Zero-Token Routing) ────────────────────────────────
# Ordem: mais específico primeiro.
_LAYER0_RULES: list[tuple[re.Pattern, str, str]] = [
    # (padrão, route, intent)
    (
        re.compile(
            r"^(oi|olá|ola|hey|bom dia|boa tarde|boa noite|tudo bem|"
            r"e aí|eaí|e ai|salve|opa|hi|hello|alô|alo)\s*[!.?]?$",
            re.IGNORECASE,
        ),
        "greeting_node",
        "intent_greeting",
    ),
    (
        re.compile(
            r"(quero\s+(mudar|alterar|atualizar|trocar)|"
            r"muda\s+meu|altera\s+meu|atualiza\s+meu|"
            r"meu\s+(email|telefone|nome)\s+é)",
            re.IGNORECASE,
        ),
        "crud_node",
        "intent_crud",
    ),
    (
        re.compile(
            r"^[!/](status|ban|unban|manutencao|prompt|cache|ingerir|help)\b",
            re.IGNORECASE,
        ),
        "admin_command_node",
        "intent_admin",
    ),
    (
        re.compile(
            r"(obrigad[ao]|valeu|tchau|até mais|até logo|ótimo|ok|"
            r"certo|perfeito|show|excelente)\s*[!.?]?$",
            re.IGNORECASE,
        ),
        "greeting_node",
        "intent_greeting",
    ),
]


class OraculoRouterService:
    """
    Cascata de roteamento em 3 camadas + Camada 0 de regex.

    Camada 0 — Regex zero-token (< 0.05 ms)
    Camada 1 — KNN Semântico async (Redis, ~5-15 ms)
    Camada 2 — PydanticRouter async (Gemini, ~300-600 ms)
    Fallback  — regex de último recurso (sempre funciona)
    """

    def __init__(
        self,
        semantic_router,    # SemanticRouterService (async)
        pydantic_router,    # PydanticRouter (async via rotear_async)
        knn_threshold: float = _KNN_THRESHOLD,
    ) -> None:
        self._semantic   = semantic_router
        self._pydantic   = pydantic_router
        self._threshold  = knn_threshold

    async def rotear(
        self,
        mensagem: str,
        contexto: dict | None = None,
        is_admin: bool = False,
    ) -> dict:
        """
        Roteia de forma totalmente async.

        Retorna dict compatível com OracleState:
          {
            "route":        str,   # nome do nó LangGraph
            "crag_score":   float, # confiança 0-1
            "_router_meta": dict,  # rastreabilidade para Langfuse
          }
        """
        t0  = time.monotonic()
        ctx = contexto or {}

        # ── Camada 0: Regex zero-token ────────────────────────────────────────
        layer0 = self._camada0_regex(mensagem, is_admin)
        if layer0:
            ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "⚡ [ROUTER L0] regex | route=%s | intent=%s | %dms",
                layer0["route"], layer0["_router_meta"]["intent"], ms,
            )
            layer0["_router_meta"]["latencia_ms"] = ms
            return layer0

        # ── Camada 1: KNN Semântico ───────────────────────────────────────────
        try:
            knn = await self._semantic.rotear(mensagem, is_admin=is_admin)
            score = getattr(knn, "score", getattr(knn, "confianca", 0.0))

            if score >= self._threshold:
                ms    = int((time.monotonic() - t0) * 1000)
                node  = _NODE_MAP.get(getattr(knn, "intent", ""), "retrieve_node")
                # Admin bypass: intent admin mas usuário não é admin
                if node == "admin_command_node" and not is_admin:
                    node = "retrieve_node"
                logger.info(
                    "🎯 [ROUTER L1] KNN | route=%s | score=%.3f | %dms",
                    node, score, ms,
                )
                return {
                    "route":       node,
                    "crag_score":  score,
                    "_router_meta": {
                        "method":      "knn_semantic",
                        "intent":      getattr(knn, "intent", "?"),
                        "score":       score,
                        "latencia_ms": ms,
                        "skip_cache":  score < 0.85,
                    },
                }

            logger.debug(
                "🤔 [ROUTER L1] KNN score %.3f < %.2f → Pydantic",
                score, self._threshold,
            )
        except Exception as exc:
            logger.warning(
                "⚠️  [ROUTER L1] KNN falhou (%s) → Pydantic",
                type(exc).__name__,
            )

        # ── Camada 2: PydanticRouter (Gemini) ─────────────────────────────────
        try:
            res = await self._pydantic.rotear_async(
                mensagem=mensagem,
                contexto_usuario=ctx,
                is_admin=is_admin,
            )
            ms   = int((time.monotonic() - t0) * 1000)
            node = _NODE_MAP.get(res.decisao, "retrieve_node")
            if res.intencao_crud:
                node = "crud_node"
            logger.info(
                "🚦 [ROUTER L2] Pydantic | route=%s | decisao=%s | conf=%.3f | %dms",
                node, res.decisao, res.confianca, ms,
            )
            return {
                "route":       node,
                "crag_score":  res.confianca,
                "_router_meta": {
                    "method":      "pydantic_ai",
                    "intent":      res.decisao,
                    "score":       res.confianca,
                    "latencia_ms": ms,
                    "skip_cache":  res.skip_cache,
                    "motivo":      res.motivo,
                },
            }
        except Exception as exc:
            logger.error(
                "❌ [ROUTER L2] Pydantic falhou (%s) → fallback regex",
                type(exc).__name__,
            )

        # ── Fallback final: regex simples ─────────────────────────────────────
        ms   = int((time.monotonic() - t0) * 1000)
        node = self._fallback_regex(mensagem)
        logger.warning(
            "⚠️  [ROUTER FALLBACK] regex | route=%s | %dms", node, ms
        )
        return {
            "route":       node,
            "crag_score":  0.0,
            "_router_meta": {
                "method":      "fallback_regex",
                "intent":      "GERAL",
                "score":       0.0,
                "latencia_ms": ms,
                "skip_cache":  True,
            },
        }

    # ── Helpers privados ───────────────────────────────────────────────────────

    def _camada0_regex(self, mensagem: str, is_admin: bool) -> dict | None:
        """
        Zero-token routing por padrão regex.
        Retorna dict de estado ou None se nenhum padrão bater.
        Admin bypass: intent admin só funciona se is_admin=True.
        """
        texto = mensagem.strip()
        for padrao, node, intent in _LAYER0_RULES:
            if padrao.search(texto):
                # Admin intent: bloqueia se não for admin
                if intent == "intent_admin" and not is_admin:
                    continue
                return {
                    "route":       node,
                    "crag_score":  1.0,
                    "_router_meta": {
                        "method":      "regex_layer0",
                        "intent":      intent,
                        "score":       1.0,
                        "latencia_ms": 0,
                        "skip_cache":  True,
                    },
                }
        return None

    @staticmethod
    def _fallback_regex(mensagem: str) -> str:
        """Regex de último recurso quando TODAS as camadas falham."""
        t = mensagem.lower()
        if re.search(r"matr[íi]cula|calend[áa]rio|prazo|semestre|aula", t):
            return "retrieve_node"
        if re.search(r"paes|vestibular|vaga|cota|inscri", t):
            return "retrieve_node"
        if re.search(r"email|telefone|contato|ctic|prog\b", t):
            return "retrieve_node"
        if re.search(r"sigaa|senha|wifi|sistema|suporte", t):
            return "retrieve_node"
        return "retrieve_node"