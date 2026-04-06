"""
src/memory/adapters/redis_long_term_memory.py
----------------------------------------------
Implementação Redis da ILongTermMemory com busca híbrida.

MELHORIAS vs long_term_memory.py anterior:
  - Implementa ILongTermMemory (interface limpa)
  - Redis injetado no construtor
  - Embeddings injetados (IEmbeddingModel interface)
  - _cosine_similarity usa numpy quando disponível, fallback puro Python
  - scan() com cursor loop robusto (o anterior podia perder keys)
  - search_hybrid() via método da interface base (MMR simplificado)
  - Fato é dataclass imutável com hash baseado no conteúdo

ESTRUTURA NO REDIS:
  ltm:list:{user_id}          → List LPUSH (Quick Recall — sem embedding)
  ltm:vec:{user_id}:{hash}    → JSON com texto + embedding (Semantic Recall)
  TTL: 30 dias
"""
from __future__ import annotations

import json
import logging
import struct
from typing import Any, Protocol

from ..ports.long_term_port import Fato, ILongTermMemory

logger = logging.getLogger(__name__)

_TTL = 86400 * 30   # 30 dias
_MAX_FATOS = 50
_PREFIX_LIST = "ltm:list:"
_PREFIX_VEC  = "ltm:vec:"


class IEmbeddingModel(Protocol):
    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class RedisLongTermMemory(ILongTermMemory):
    """
    Long-Term Memory com duas camadas:
      - Lista LIFO para Quick Recall (sem custo de embedding)
      - JSON com vetor para Semantic Recall
    """

    def __init__(self, redis_client: Any, embedding_model: IEmbeddingModel):
        self._r = redis_client
        self._emb = embedding_model

    # ─────────────────────────────────────────────────────────────────────────
    # ILongTermMemory implementation
    # ─────────────────────────────────────────────────────────────────────────

    def save(self, user_id: str, fato: Fato) -> bool:
        """Persiste fato. Retorna True se novo, False se duplicado."""
        vec_key = f"{_PREFIX_VEC}{user_id}:{fato.hash_id}"
        try:
            if self._r.exists(vec_key):
                return False

            # Gera embedding
            try:
                vetor = self._emb.embed_query(fato.texto)
            except Exception as e:
                logger.warning("⚠️  LTM: embedding falhou para [%s]: %s", user_id, e)
                vetor = []

            # Persiste JSON vetorial
            self._r.json().set(vec_key, "$", {
                "texto": fato.texto,
                "user_id": user_id,
                "timestamp": fato.timestamp,
                "source": fato.source,
                "embedding": vetor,
            })
            self._r.expire(vec_key, _TTL)

            # Persiste na lista de Quick Recall
            list_key = f"{_PREFIX_LIST}{user_id}"
            self._r.lpush(list_key, fato.texto)
            self._r.ltrim(list_key, 0, _MAX_FATOS - 1)
            self._r.expire(list_key, _TTL)

            logger.info("💾 LTM salvo [%s]: %.70s", user_id, fato.texto)
            return True

        except Exception as e:
            logger.error("❌ LTM.save [%s]: %s", user_id, e)
            return False

    def save_batch(self, user_id: str, fatos: list[Fato]) -> int:
        return sum(1 for f in fatos if self.save(user_id, f))

    def search_recent(self, user_id: str, limit: int = 5) -> list[Fato]:
        try:
            textos = self._r.lrange(f"{_PREFIX_LIST}{user_id}", 0, limit - 1)
            return [
                Fato(texto=t if isinstance(t, str) else t.decode(), user_id=user_id)
                for t in textos if t
            ]
        except Exception:
            return []

    def search_semantic(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.65,
    ) -> list[Fato]:
        """Busca por similaridade coseno local (sem índice vetorial adicional)."""
        try:
            vetor_q = self._emb.embed_query(query)
        except Exception as e:
            logger.warning("⚠️  LTM.search_semantic embedding falhou: %s", e)
            return self.search_recent(user_id, limit)

        todos = self._load_all_with_embeddings(user_id)
        if not todos:
            return self.search_recent(user_id, limit)

        scored: list[tuple[float, Fato]] = []
        for item in todos:
            emb = item.get("embedding", [])
            if not emb:
                continue
            score = _cosine(vetor_q, emb)
            if score >= threshold:
                scored.append((score, Fato(
                    texto=item.get("texto", ""),
                    user_id=user_id,
                    timestamp=item.get("timestamp", 0),
                    relevance_score=score,
                    source=item.get("source", "extractor"),
                )))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:limit]]

    def list_all(self, user_id: str, limit: int = 50) -> list[Fato]:
        textos = self._r.lrange(f"{_PREFIX_LIST}{user_id}", 0, limit - 1)
        return [
            Fato(texto=t if isinstance(t, str) else t.decode(), user_id=user_id)
            for t in textos if t
        ]

    def delete_all(self, user_id: str) -> None:
        self._r.delete(f"{_PREFIX_LIST}{user_id}")
        # Remove todas as keys vetoriais
        cursor = 0
        while True:
            cursor, keys = self._r.scan(
                cursor, match=f"{_PREFIX_VEC}{user_id}:*", count=100
            )
            if keys:
                self._r.delete(*keys)
            if cursor == 0:
                break
        logger.info("🗑️  LTM deletado: %s", user_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers privados
    # ─────────────────────────────────────────────────────────────────────────

    def _load_all_with_embeddings(self, user_id: str) -> list[dict]:
        """Carrega todos os fatos com embedding para busca semântica local."""
        resultado = []
        cursor = 0
        while True:
            cursor, keys = self._r.scan(
                cursor, match=f"{_PREFIX_VEC}{user_id}:*", count=100
            )
            for key in keys:
                try:
                    doc = self._r.json().get(key, "$")
                    if doc:
                        item = doc[0] if isinstance(doc, list) else doc
                        resultado.append(item)
                except Exception:
                    pass
            if cursor == 0:
                break
        return resultado


def _cosine(v1: list[float], v2: list[float]) -> float:
    """
    Similaridade coseno para vetores normalizados.
    Usa numpy quando disponível para velocidade, fallback Python puro.
    """
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    try:
        import numpy as np
        a, b = np.array(v1, dtype=np.float32), np.array(v2, dtype=np.float32)
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)
    except ImportError:
        # Fallback Python puro (para vetores já normalizados, produto escalar = coseno)
        dot = sum(a * b for a, b in zip(v1, v2))
        return max(0.0, min(1.0, dot))