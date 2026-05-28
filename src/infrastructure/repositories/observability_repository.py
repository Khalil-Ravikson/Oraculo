"""
src/infrastructure/repositories/observability_repository.py
=============================================================
Repository para persistir métricas, audit e feedback no PostgreSQL.

SUBSTITUI as seguintes keys Redis:
  metrics:respostas   → metricas_llm
  audit:log           → audit_log
  feedback:ratings    → feedback_avaliacoes

COMPATIBILIDADE RETROATIVA:
  Mantém escrita no Redis (histórico curto para dashboard em tempo real)
  E grava no PostgreSQL (histórico longo para análise).

  Isso garante zero downtime na migração:
    Fase 1 (agora): dual-write Redis + Postgres
    Fase 2 (futuro): remover escrita Redis após confirmar Postgres estável

PADRÃO:
  Métodos async (FastAPI/LangGraph) e sync (Celery workers via asyncio.run).
  Sem estado global — injetar a session SQLAlchemy.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ObservabilityRepository:
    """
    Persiste métricas de observabilidade no PostgreSQL.
    Usa queries SQL raw para evitar dependência de models Alembic aqui.
    """

    def __init__(self, session: AsyncSession):
        self._db = session

    # ─── Métricas LLM ─────────────────────────────────────────────────────────

    async def salvar_metrica_llm(
        self,
        user_id:        str,
        rota:           str,
        tokens_entrada: int,
        tokens_saida:   int,
        latencia_ms:    int,
        crag_score:     float = 0.0,
        cache_hit:      bool  = False,
        cache_layer:    str   = "",
        chunks_count:   int   = 0,
        custo_usd:      float = 0.0,
        modelo:         str   = "",
    ) -> None:
        try:
            await self._db.execute(
                text("""
                    INSERT INTO metricas_llm
                        (user_id, rota, tokens_entrada, tokens_saida, tokens_total,
                         latencia_ms, crag_score, cache_hit, cache_layer,
                         chunks_count, custo_usd, modelo)
                    VALUES
                        (:user_id, :rota, :tok_in, :tok_out, :tok_total,
                         :lat, :crag, :cache_hit, :cache_layer,
                         :chunks, :custo, :modelo)
                """),
                {
                    "user_id":     user_id[-20:] if user_id else None,
                    "rota":        rota[:20] if rota else None,
                    "tok_in":      tokens_entrada,
                    "tok_out":     tokens_saida,
                    "tok_total":   tokens_entrada + tokens_saida,
                    "lat":         latencia_ms,
                    "crag":        round(crag_score, 4),
                    "cache_hit":   cache_hit,
                    "cache_layer": cache_layer[:10] if cache_layer else None,
                    "chunks":      chunks_count,
                    "custo":       round(custo_usd, 8),
                    "modelo":      modelo[:50] if modelo else None,
                },
            )
            await self._db.commit()
        except Exception as e:
            logger.error("❌ [OBS] salvar_metrica_llm falhou: %s", e)
            await self._db.rollback()

    async def get_metricas_dashboard(self, horas: int = 24) -> dict:
        """Retorna métricas agregadas para o dashboard admin."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        COUNT(*)                            AS total_msgs,
                        SUM(tokens_total)                  AS tokens_total,
                        ROUND(SUM(custo_usd)::numeric, 4)  AS custo_usd,
                        ROUND(AVG(latencia_ms))             AS latencia_media_ms,
                        ROUND(AVG(crag_score)::numeric, 3)  AS crag_medio,
                        ROUND(
                          100.0 * SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                          1
                        )                                  AS cache_hit_pct,
                        COUNT(DISTINCT user_id)            AS usuarios_unicos
                    FROM metricas_llm
                    WHERE ts >= NOW() - INTERVAL ':horas hours'
                """),
                {"horas": horas},
            )
            row = result.fetchone()
            if row:
                return dict(row._mapping)
        except Exception as e:
            logger.error("❌ [OBS] get_metricas_dashboard falhou: %s", e)
        return {}

    async def get_metricas_por_rota(self, horas: int = 24) -> list[dict]:
        """Distribuição de chamadas por rota nas últimas N horas."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        rota,
                        COUNT(*)                AS total,
                        ROUND(AVG(latencia_ms)) AS latencia_media_ms,
                        ROUND(SUM(custo_usd)::numeric, 6) AS custo_usd,
                        ROUND(AVG(crag_score)::numeric, 3) AS crag_medio
                    FROM metricas_llm
                    WHERE ts >= NOW() - INTERVAL ':horas hours'
                    GROUP BY rota
                    ORDER BY total DESC
                """),
                {"horas": horas},
            )
            return [dict(r._mapping) for r in result.fetchall()]
        except Exception as e:
            logger.error("❌ [OBS] get_metricas_por_rota falhou: %s", e)
        return []

    # ─── Audit Log ────────────────────────────────────────────────────────────

    async def salvar_audit(
        self,
        admin_id:  str,
        action:    str,
        resultado: str = "ok",
        target:    str = "",
        detalhes:  dict | None = None,
        ip:        str = "",
    ) -> None:
        try:
            await self._db.execute(
                text("""
                    INSERT INTO audit_log (admin_id, action, target, resultado, detalhes, ip)
                    VALUES (:admin, :action, :target, :resultado, :detalhes::jsonb, :ip)
                """),
                {
                    "admin":     admin_id[:50] if admin_id else None,
                    "action":    action[:100],
                    "target":    target[:100] if target else None,
                    "resultado": resultado[:50],
                    "detalhes":  json.dumps(detalhes, ensure_ascii=False) if detalhes else None,
                    "ip":        ip[:45] if ip else None,
                },
            )
            await self._db.commit()
        except Exception as e:
            logger.error("❌ [OBS] salvar_audit falhou: %s", e)
            await self._db.rollback()

    async def get_audit_logs(self, limit: int = 100) -> list[dict]:
        try:
            result = await self._db.execute(
                text("""
                    SELECT ts, admin_id, action, target, resultado, detalhes
                    FROM audit_log
                    ORDER BY ts DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )
            rows = result.fetchall()
            return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.error("❌ [OBS] get_audit_logs falhou: %s", e)
        return []

    # ─── Feedback ─────────────────────────────────────────────────────────────

    async def salvar_feedback(
        self,
        user_id:    str,
        rating:     int,
        rota:       str = "",
        crag_score: float = 0.0,
        session_id: str = "",
        comentario: str = "",
    ) -> None:
        if not 1 <= rating <= 5:
            logger.warning("⚠️  Rating inválido: %d", rating)
            return
        try:
            await self._db.execute(
                text("""
                    INSERT INTO feedback_avaliacoes
                        (user_id, rating, rota, crag_score, session_id, comentario)
                    VALUES (:uid, :rating, :rota, :crag, :sess, :coment)
                """),
                {
                    "uid":    user_id[-20:] if user_id else None,
                    "rating": rating,
                    "rota":   rota[:20] if rota else None,
                    "crag":   round(crag_score, 4),
                    "sess":   session_id[-20:] if session_id else None,
                    "coment": comentario[:500] if comentario else None,
                },
            )
            await self._db.commit()
        except Exception as e:
            logger.error("❌ [OBS] salvar_feedback falhou: %s", e)
            await self._db.rollback()

    async def get_nps_summary(self, horas: int = 168) -> dict:
        """NPS e distribuição de ratings na última semana."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        ROUND(AVG(rating)::numeric, 2) AS media,
                        SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS positivos,
                        SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS negativos,
                        json_object_agg(rating::text, cnt) AS distribuicao
                    FROM (
                        SELECT rating, COUNT(*) AS cnt
                        FROM feedback_avaliacoes
                        WHERE ts >= NOW() - INTERVAL ':horas hours'
                        GROUP BY rating
                    ) t
                """),
                {"horas": horas},
            )
            row = result.fetchone()
            if row:
                data = dict(row._mapping)
                total = data.get("total") or 1
                data["nps"] = round(
                    ((data.get("positivos", 0) - data.get("negativos", 0)) / total) * 100, 1
                )
                return data
        except Exception as e:
            logger.error("❌ [OBS] get_nps_summary falhou: %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Helper síncrono para Celery workers
# ─────────────────────────────────────────────────────────────────────────────

def salvar_metrica_sync(
    user_id:        str,
    rota:           str,
    tokens_entrada: int,
    tokens_saida:   int,
    latencia_ms:    int,
    crag_score:     float = 0.0,
    cache_hit:      bool  = False,
    cache_layer:    str   = "",
    chunks_count:   int   = 0,
    custo_usd:      float = 0.0,
    modelo:         str   = "",
) -> None:
    """
    Versão síncrona para uso em tasks Celery.
    Usa asyncio.run() dentro de thread — seguro para Celery.
    """
    import asyncio
    from src.infrastructure.database.session import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as session:
            repo = ObservabilityRepository(session)
            await repo.salvar_metrica_llm(
                user_id=user_id, rota=rota,
                tokens_entrada=tokens_entrada, tokens_saida=tokens_saida,
                latencia_ms=latencia_ms, crag_score=crag_score,
                cache_hit=cache_hit, cache_layer=cache_layer,
                chunks_count=chunks_count, custo_usd=custo_usd,
                modelo=modelo,
            )

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("❌ [OBS] salvar_metrica_sync falhou: %s", e)


def salvar_audit_sync(admin_id: str, action: str, resultado: str = "ok", detalhes: dict | None = None) -> None:
    """Versão síncrona do audit log para Celery."""
    import asyncio
    from src.infrastructure.database.session import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as session:
            repo = ObservabilityRepository(session)
            await repo.salvar_audit(admin_id=admin_id, action=action, resultado=resultado, detalhes=detalhes)

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("❌ [OBS] salvar_audit_sync falhou: %s", e)