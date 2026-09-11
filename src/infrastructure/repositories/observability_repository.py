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
        provider:       str   = "",
    ) -> None:
        try:
            await self._db.execute(
                text("""
                    INSERT INTO metricas_llm
                        (user_id, rota, tokens_entrada, tokens_saida, tokens_total,
                         latencia_ms, crag_score, cache_hit, cache_layer,
                         chunks_count, custo_usd, modelo, provider)
                    VALUES
                        (:user_id, :rota, :tok_in, :tok_out, :tok_total,
                         :lat, :crag, :cache_hit, :cache_layer,
                         :chunks, :custo, :modelo, :provider)
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
                    "provider":    provider[:20] if provider else None,
                },
            )
            await self._db.commit()
        except Exception as e:
            logger.error("❌ [OBS] salvar_metrica_llm falhou: %s", e)
            await self._db.rollback()

    async def salvar_uso_llm(self, uso) -> None:
        """Grava um `UsoLLM` (ver `observability/usage.py`).

        Substitui `salvar_metrica_llm` para o caminho novo: aquele recebia 12
        argumentos soltos e não tinha onde pôr custo aberto, origem do preço
        nem status. Continua existindo para os call sites antigos (avaliador,
        RAG) que gravam métrica sem passar por um provider de LLM."""
        try:
            await self._db.execute(text(_SQL_USO_LLM), uso.para_persistencia())
            await self._db.commit()
        except Exception as e:
            logger.error("❌ [OBS] salvar_uso_llm falhou: %s", e)
            await self._db.rollback()

    async def get_qualidade_custo(self, horas: int = 24) -> dict:
        """Quanto do gasto do período é medido, estimado ou desconhecido.

        Responde a pergunta que o painel não conseguia responder: antes da
        migration 027, custo desconhecido era gravado como `0.0` e somava com
        as chamadas realmente gratuitas. Um provider novo sem preço
        cadastrado aparecia como se não custasse nada.

        Linhas antigas (anteriores à migration) não têm `cost_status` — entram
        como `legado`, e não como desconhecidas: elas foram calculadas, só não
        registraram a confiança."""
        try:
            resultado = await self._db.execute(
                text("""
                    SELECT
                        COALESCE(cost_status, 'legado')      AS status,
                        COALESCE(pricing_source, 'legado')   AS origem,
                        COUNT(*)                             AS chamadas,
                        COALESCE(SUM(custo_usd), 0)          AS custo_usd,
                        COALESCE(SUM(tokens_total), 0)       AS tokens
                    FROM metricas_llm
                    -- `make_interval`, e não concatenação de string: com
                    -- asyncpg o parâmetro chega tipado, e `:horas || ' hours'`
                    -- exige str enquanto o endpoint passa int.
                    WHERE ts >= NOW() - make_interval(hours => :horas)
                    GROUP BY 1, 2
                    ORDER BY chamadas DESC
                """),
                {"horas": horas},
            )
        except Exception as e:
            logger.error("❌ [OBS] get_qualidade_custo falhou: %s", e)
            return {"por_status": [], "tokens_estimados_pct": 0.0}

        linhas = [dict(r._mapping) for r in resultado]
        for linha in linhas:
            linha["custo_usd"] = float(linha["custo_usd"] or 0)

        total = sum(l["chamadas"] for l in linhas) or 1
        nao_exatas = sum(l["chamadas"] for l in linhas if l["status"] not in ("exact", "legado"))

        return {
            "por_status": linhas,
            # Percentual do período cujo custo NÃO é medido com preço oficial
            # e tokens reais. É o número que diz o quanto confiar no total.
            "estimado_ou_desconhecido_pct": round(nao_exatas / total * 100, 1),
        }

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
                    WHERE ts >= NOW() - make_interval(hours => :horas)
                """),
                {"horas": horas},
            )
            row = result.fetchone()
            if row:
                return dict(row._mapping)
        except Exception as e:
            logger.error("❌ [OBS] get_metricas_dashboard falhou: %s", e)
        return {}

    async def get_serie_horaria(self, horas: int = 24) -> list[dict]:
        """Série por hora (para sparklines da página de custo). Buckets sem
        dado não aparecem — o front interpola visualmente."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        date_trunc('hour', ts)                       AS hora,
                        COUNT(*)                                     AS msgs,
                        COALESCE(SUM(tokens_total), 0)               AS tokens,
                        ROUND(COALESCE(SUM(custo_usd), 0)::numeric, 4) AS custo_usd,
                        ROUND(AVG(latencia_ms))                       AS latencia_ms,
                        ROUND(
                          100.0 * SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1
                        )                                            AS cache_hit_pct
                    FROM metricas_llm
                    WHERE ts >= NOW() - make_interval(hours => :horas)
                    GROUP BY 1 ORDER BY 1
                """),
                {"horas": horas},
            )
            return [
                {
                    "hora": r._mapping["hora"].isoformat() if r._mapping["hora"] else None,
                    "msgs": int(r._mapping["msgs"] or 0),
                    "tokens": int(r._mapping["tokens"] or 0),
                    "custo_usd": float(r._mapping["custo_usd"] or 0),
                    "latencia_ms": int(r._mapping["latencia_ms"] or 0),
                    "cache_hit_pct": float(r._mapping["cache_hit_pct"] or 0),
                }
                for r in result.fetchall()
            ]
        except Exception as e:
            logger.error("❌ [OBS] get_serie_horaria falhou: %s", e)
            return []

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
                    WHERE ts >= NOW() - make_interval(hours => :horas)
                    GROUP BY rota
                    ORDER BY total DESC
                """),
                {"horas": horas},
            )
            return [dict(r._mapping) for r in result.fetchall()]
        except Exception as e:
            logger.error("❌ [OBS] get_metricas_por_rota falhou: %s", e)
        return []

    async def get_metricas_por_provider(self, horas: int = 24) -> list[dict]:
        """Distribuição de chamadas/custo por provider LLM (Gemini/DeepSeek/Groq)
        nas últimas N horas — base da página de custo do /hub e do dashboard
        Grafana "LLM Custo & Providers"."""
        try:
            result = await self._db.execute(
                text("""
                    SELECT
                        COALESCE(provider, 'desconhecido') AS provider,
                        COUNT(*)                            AS total,
                        SUM(tokens_total)                  AS tokens_total,
                        SUM(tokens_entrada)                AS tokens_entrada,
                        SUM(tokens_saida)                  AS tokens_saida,
                        ROUND(
                          100.0 * SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                          1
                        )                                  AS cache_hit_pct,
                        ROUND(SUM(custo_usd)::numeric, 6)  AS custo_usd,
                        ROUND(AVG(latencia_ms))             AS latencia_media_ms
                    FROM metricas_llm
                    WHERE ts >= NOW() - make_interval(hours => :horas)
                    GROUP BY provider
                    ORDER BY custo_usd DESC
                """),
                {"horas": horas},
            )
            return [dict(r._mapping) for r in result.fetchall()]
        except Exception as e:
            logger.error("❌ [OBS] get_metricas_por_provider falhou: %s", e)
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
                    VALUES (:admin, :action, :target, :resultado, CAST(:detalhes AS jsonb), :ip)
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
                        WHERE ts >= NOW() - make_interval(hours => :horas)
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

# ─────────────────────────────────────────────────────────────────────────────
# Telemetria de uso de LLM (migration 027)
#
# `ON CONFLICT DO NOTHING` no `request_id` é a idempotência: um retry de task
# que reenvie o mesmo evento não duplica a linha. Sem isso, uma task que falha
# DEPOIS de gravar a métrica e é reexecutada contaria o mesmo gasto duas vezes.
# ─────────────────────────────────────────────────────────────────────────────

_SQL_USO_LLM = """
    INSERT INTO metricas_llm
        (request_id, trace_id, user_id, rota, provider, modelo,
         tokens_entrada, tokens_saida, tokens_total, tokens_cache, tokens_reasoning,
         custo_entrada, custo_saida, custo_cache, custo_reasoning, custo_usd,
         moeda, pricing_source, cost_status, token_count_status, pricing_version,
         latencia_ms, status, erro)
    VALUES
        (:request_id, :trace_id, :user_id, :rota, :provider, :modelo,
         :tokens_entrada, :tokens_saida, :tokens_total, :tokens_cache, :tokens_reasoning,
         :custo_entrada, :custo_saida, :custo_cache, :custo_reasoning, :custo_usd,
         :moeda, :pricing_source, :cost_status, :token_count_status, :pricing_version,
         :latencia_ms, :status, :erro)
    ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
"""


def salvar_uso_llm_sync(uso) -> None:
    """Grava um `UsoLLM` (ver `observability/usage.py`) pelo caminho síncrono.

    Nunca levanta: telemetria é secundária à resposta do LLM."""
    try:
        from sqlalchemy import create_engine
        from src.infrastructure.settings import settings

        url = settings.DATABASE_URL.replace("+asyncpg", "").replace(
            "postgresql://", "postgresql+psycopg2://"
        )
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.begin() as conn:
                conn.execute(text(_SQL_USO_LLM), uso.para_persistencia())
        finally:
            engine.dispose()
    except Exception as e:
        logger.error("❌ [OBS] salvar_uso_llm_sync falhou: %s", e)


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
    provider:       str   = "",
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
                modelo=modelo, provider=provider,
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