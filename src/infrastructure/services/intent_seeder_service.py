"""
src/infrastructure/services/intent_seeder_service.py
------------------------------------------------------
SERVICE PURO — sem Celery, sem FastAPI, sem estado global.
Responsabilidade única: carregar intents_router do Postgres → Redis no boot.

FLUXO:
  1. Lê todas as intents ativas do Postgres
  2. Para cada intent com exemplos: gera embeddings (batch)
  3. Salva em Redis JSON (idx:tools) para o router KNN usar
  4. Armazena também regex compilado em Redis hash (router:regex)

GARANTIAS:
  - Idempotente: re-executar não duplica dados (upsert por nome)
  - Degradação graciosa: se Gemini falhar, salva só o regex (KNN fica sem vetores)
  - CPU-only: embedding via Gemini API (nuvem), sem GPU local

USO (no startup FastAPI):
    from src.infrastructure.services.intent_seeder_service import IntentSeederService
    await IntentSeederService().seed()
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

REDIS_REGEX_KEY  = "router:regex"        # Hash: nome → regex
REDIS_CONFIG_KEY = "router:config"       # Hash: nome → JSON config (doc_type, k...)
PREFIX_TOOLS     = "tools:emb:"          # Prefix dos embeddings no RedisVL


@dataclass
class SeedResult:
    total_intents: int = 0
    intents_com_vetores: int = 0
    intents_so_regex: int = 0
    erros: list[str] = None

    def __post_init__(self):
        if self.erros is None:
            self.erros = []


class IntentSeederService:
    """
    Service puro para seed de intents.
    Não acessa Redis ou Postgres diretamente — recebe por injeção.
    """

    async def seed(
        self,
        db: AsyncSession | None = None,
        redis_client: Any = None,
        embedding_model: Any = None,
    ) -> SeedResult:
        """
        Seed completo: Postgres → Redis.
        Se db/redis/embedding não forem fornecidos, usa singletons da infraestrutura.
        """
        if db is None:
            from src.infrastructure.database.session import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                return await self._executar_seed(session, redis_client, embedding_model)
        return await self._executar_seed(db, redis_client, embedding_model)

    async def _executar_seed(
        self,
        db: AsyncSession,
        redis_client: Any,
        embedding_model: Any,
    ) -> SeedResult:
        from src.infrastructure.database.models import IntentRouter

        result = SeedResult()

        # Lazy imports para não poluir boot
        if redis_client is None:
            from src.infrastructure.redis_client import get_redis
            redis_client = get_redis()

        if embedding_model is None:
            from src.rag.embeddings import get_embeddings
            embedding_model = get_embeddings()

        # 1. Busca intents ativas
        rows = (await db.execute(
            select(IntentRouter).where(IntentRouter.ativo == True)
        )).scalars().all()

        if not rows:
            logger.warning("⚠️  [INTENT SEEDER] Nenhuma intent ativa no banco.")
            return result

        result.total_intents = len(rows)
        logger.info("🌱 [INTENT SEEDER] Seeding %d intents...", len(rows))

        # 2. Limpa vetores antigos (re-seed idempotente)
        self._limpar_vetores_antigos(redis_client)

        # 3. Salva regex e config em Redis Hash (sem embedding — zero custo)
        pipe = redis_client.pipeline()
        for intent in rows:
            if intent.regex:
                pipe.hset(REDIS_REGEX_KEY, intent.nome, intent.regex)
            config = json.dumps({
                "doc_type": intent.doc_type or "geral",
                "k_vector": intent.k_vector or 6,
                "k_text":   intent.k_text or 8,
            })
            pipe.hset(REDIS_CONFIG_KEY, intent.nome, config)
        pipe.execute()

        # 4. Gera embeddings por intent (batch para economizar calls)
        for intent in rows:
            if not intent.exemplos:
                result.intents_so_regex += 1
                continue

            try:
                vetores = await self._embeddings_batch(
                    embedding_model, intent.exemplos
                )
                self._salvar_vetores(redis_client, intent.nome, intent.exemplos, vetores)
                result.intents_com_vetores += 1
                logger.info(
                    "  ✅ %s: %d exemplos vetorizados", intent.nome, len(vetores)
                )
            except Exception as e:
                result.intents_so_regex += 1
                result.erros.append(f"{intent.nome}: {e}")
                logger.warning(
                    "  ⚠️  %s: embedding falhou (%s), usando só regex", intent.nome, e
                )

        logger.info(
            "🌱 [INTENT SEEDER] Concluído | %d intents | %d c/vetores | %d só regex | %d erros",
            result.total_intents, result.intents_com_vetores,
            result.intents_so_regex, len(result.erros),
        )
        return result

    async def _embeddings_batch(
        self,
        model: Any,
        textos: list[str],
    ) -> list[list[float]]:
        """Gera embeddings via Gemini API (nuvem, CPU-friendly)."""
        import asyncio
        return await asyncio.to_thread(model.embed_documents, textos)

    def _salvar_vetores(
        self,
        r: Any,
        nome_intent: str,
        exemplos: list[str],
        vetores: list[list[float]],
    ) -> None:
        """Salva embeddings no Redis JSON para uso pelo KNN router."""
        for i, (exemplo, vetor) in enumerate(zip(exemplos, vetores)):
            key = f"{PREFIX_TOOLS}{nome_intent}:{i}"
            r.json().set(key, "$", {
                "name":        nome_intent,
                "description": exemplo,
                "embedding":   vetor,
            })

    def _limpar_vetores_antigos(self, r: Any) -> None:
        """Remove vetores antigos antes de re-seed."""
        cursor = 0
        keys_deletadas = 0
        while True:
            cursor, keys = r.scan(cursor, match=f"{PREFIX_TOOLS}*", count=200)
            if keys:
                r.delete(*keys)
                keys_deletadas += len(keys)
            if cursor == 0:
                break
        if keys_deletadas:
            logger.info("🗑️  [INTENT SEEDER] %d vetores antigos removidos.", keys_deletadas)