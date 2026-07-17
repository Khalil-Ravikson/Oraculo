"""
src/application/tasks/beat_nightly_memory.py
=============================================
Celery Beat Task — Sincronização Noturna de Memória Episódica.

CONTROLE: variável ENABLE_NIGHTLY_MEMORY no .env (default: False).
Se False: a task executa mas retorna imediatamente sem fazer nada.
Fácil de toglar no Dashboard sem reiniciar workers.

FLUXO (quando habilitada):
  1. Busca conversas do dia no Redis (chat:{session_id})
  2. Para cada utilizador ativo: sintetiza fatos relevantes com Gemini Flash
  3. Salva fatos novos na LongTermMemory (Redis JSON + lista LIFO)
  4. Remove fatos duplicados / obsoletos
  5. Grava snapshot de métricas na tabela monitor_snapshots

MÉTRICAS:
  oraculo_nightly_memory_users_processed_total
  oraculo_nightly_memory_facts_created_total
  oraculo_nightly_memory_duration_seconds
"""
from __future__ import annotations

import json
import logging
import time

from prometheus_client import Counter, Gauge

from src.infrastructure.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Métricas ──────────────────────────────────────────────────────────────────
_USERS_PROCESSED = Counter(
    "oraculo_nightly_memory_users_processed_total",
    "Utilizadores processados na sync noturna",
)
_FACTS_CREATED = Counter(
    "oraculo_nightly_memory_facts_created_total",
    "Fatos novos criados pela sync noturna",
)
_DURATION = Gauge(
    "oraculo_nightly_memory_duration_seconds",
    "Duração da última execução da sync noturna",
)

# Prompt de síntese de fatos
_PROMPT_SINTETIZAR = """Analise as conversas do dia abaixo e extraia fatos ESTÁTICOS e PERSISTENTES sobre o aluno.

REGRAS:
- Foque em: Curso, Centro, Período/Turno, Problemas recorrentes, Preferências de comunicação
- Ignore: perguntas pontuais, saudações, informações efêmeras
- NÃO descreva a conversa — extraia fatos SOBRE O ALUNO
- Máximo 5 fatos. Se não houver, retorne lista vazia.
- Formato: lista de strings descritivas, sem prefixos.

<conversas_do_dia>
{conversas}
</conversas_do_dia>

Responda APENAS com JSON: {"fatos": ["fato1", "fato2"]}"""


@celery_app.task(
    name="beat_nightly_memory_sync",
    bind=True,
    queue="default",
)
def beat_nightly_memory_sync(self) -> dict:
    """
    Task Celery Beat executada diariamente às 02:00.
    Controlada por ENABLE_NIGHTLY_MEMORY no .env.
    """
    import asyncio
    return asyncio.run(_executar_sync())


async def _executar_sync() -> dict:
    from src.infrastructure.settings import settings

    # ── FLAG DE CONTROLE ───────────────────────────────────────────────────────
    # Verifica também no Redis para permitir toggle em tempo real via Dashboard
    habilitado = _verificar_flag_habilitada()

    if not habilitado:
        logger.info("ℹ️  [NIGHTLY MEMORY] Desabilitado (ENABLE_NIGHTLY_MEMORY=False). Pulando.")
        return {"status": "disabled", "users": 0, "facts": 0}

    t0 = time.monotonic()
    logger.info("🌙 [NIGHTLY MEMORY] Iniciando sincronização noturna...")

    total_users = 0
    total_facts = 0

    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()

        # Busca todos os session_ids com atividade recente (chaves chat:*)
        cursor = 0
        session_keys = []
        while True:
            cursor, keys = r.scan(cursor, match="chat:*", count=500)
            session_keys.extend(keys)
            if cursor == 0:
                break

        logger.info("🌙 [NIGHTLY MEMORY] %d sessões encontradas", len(session_keys))

        for key in session_keys:
            try:
                session_id = (key.decode() if isinstance(key, bytes) else key).replace("chat:", "")

                # Carrega conversas do dia
                raw_msgs = r.lrange(key, -30, -1)  # últimas 30 mensagens
                if len(raw_msgs) < 4:
                    continue  # conversa muito curta, não vale processar

                conversas_txt = _formatar_conversas(raw_msgs)
                if len(conversas_txt) < 100:
                    continue

                # Sintetiza fatos com Gemini Flash
                novos_fatos = await _sintetizar_fatos_flash(session_id, conversas_txt)

                if novos_fatos:
                    salvos = _salvar_fatos(session_id, novos_fatos, r)
                    total_facts += salvos
                    _FACTS_CREATED.inc(salvos)

                total_users += 1
                _USERS_PROCESSED.inc()

            except Exception as e:
                logger.warning("⚠️  [NIGHTLY MEMORY] Falha na sessão %s: %s",
                               str(key)[-12:], e)

        # Snapshot de métricas
        await _salvar_snapshot_metricas(total_users, total_facts)

    except Exception as exc:
        logger.exception("❌ [NIGHTLY MEMORY] Falha geral: %s", exc)

    elapsed = time.monotonic() - t0
    _DURATION.set(elapsed)

    logger.info(
        "🌙 [NIGHTLY MEMORY] Concluído | %d utilizadores | %d fatos | %.1fs",
        total_users, total_facts, elapsed,
    )
    return {"status": "ok", "users": total_users, "facts": total_facts, "elapsed_s": round(elapsed, 1)}


def _verificar_flag_habilitada() -> bool:
    """
    Verifica a flag ENABLE_NIGHTLY_MEMORY:
    1. Primeiro no Redis (permite toggle em tempo real via Dashboard)
    2. Depois no .env (configuração estática)
    """
    # Verificação no Redis (prioridade — Dashboard pode alterar em runtime)
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        flag_redis = r.get("admin:nightly_memory_enabled")
        if flag_redis is not None:
            val = flag_redis if isinstance(flag_redis, str) else flag_redis.decode()
            return val.lower() in ("1", "true", "yes")
    except Exception:
        pass

    # Fallback: variável de ambiente
    import os
    return os.getenv("ENABLE_NIGHTLY_MEMORY", "false").lower() in ("1", "true", "yes")


def _formatar_conversas(raw_msgs: list) -> str:
    """Converte mensagens Redis para texto legível."""
    linhas = []
    for item in raw_msgs:
        try:
            raw = item.decode() if isinstance(item, bytes) else item
            d = json.loads(raw)
            prefixo = "Aluno" if d.get("role") == "user" else "Bot"
            linhas.append(f"{prefixo}: {d.get('content', '')[:200]}")
        except Exception:
            continue
    return "\n".join(linhas)


async def _sintetizar_fatos_flash(session_id: str, conversas: str) -> list[str]:
    """Chama Gemini Flash para extrair fatos das conversas do dia."""
    from src.infrastructure.settings import settings
    import google.genai as genai
    from google.genai import types

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=_PROMPT_SINTETIZAR.format(conversas=conversas[:2000]),
            config=types.GenerateContentConfig(
                temperature=0.05,
                max_output_tokens=200,
                response_mime_type="application/json",
            ),
        )
        data = json.loads(response.text or "{}")
        fatos = data.get("fatos", [])
        return [f for f in fatos if isinstance(f, str) and len(f) >= 15]

    except Exception as e:
        logger.warning("⚠️  [NIGHTLY] Flash falhou para %s: %s", session_id[-6:], e)
        return []


def _salvar_fatos(session_id: str, fatos: list[str], redis_client) -> int:
    """Salva fatos novos na memória de longo prazo (lista LIFO no Redis)."""
    prefix = "mem:facts:list:"
    key = f"{prefix}{session_id}"
    saved = 0

    # Busca fatos existentes para deduplicação
    existentes = set()
    try:
        raw_existentes = redis_client.lrange(key, 0, 49)
        for item in raw_existentes:
            txt = item.decode() if isinstance(item, bytes) else item
            existentes.add(txt.lower()[:50])
    except Exception:
        pass

    for fato in fatos:
        if fato.lower()[:50] not in existentes:
            redis_client.lpush(key, fato)
            redis_client.ltrim(key, 0, 49)  # max 50 fatos por utilizador
            redis_client.expire(key, 86400 * 30)  # 30 dias TTL
            existentes.add(fato.lower()[:50])
            saved += 1

    return saved


async def _salvar_snapshot_metricas(users: int, facts: int) -> None:
    """Grava snapshot na tabela monitor_snapshots do PostgreSQL."""
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    INSERT INTO monitor_snapshots
                        (usuarios_ativos_1h, msgs_processadas_1h,
                         tokens_1h, custo_usd_1h)
                    VALUES (:users, :facts, 0, 0)
                """),
                {"users": users, "facts": facts},
            )
            await db.commit()
    except Exception as e:
        logger.warning("⚠️  [NIGHTLY] Snapshot Postgres falhou: %s", e)