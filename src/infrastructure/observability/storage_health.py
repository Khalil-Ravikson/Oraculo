"""
src/infrastructure/observability/storage_health.py — saúde de Redis + Postgres
=============================================================================
Alimenta o painel `/hub/infra/storage` (Hub v2, Sprint 5) — um "RedisInsight
light" com só o que o Oráculo usa: memória, hit rate, clientes, ops/s,
keyspace, política de despejo, módulos Redis Stack carregados, slowlog,
persistência (RDB/AOF), e stats do Postgres.

Tudo síncrono (usa `get_redis_text`) e fail-safe: qualquer falha vira um
campo `erro` no dict, nunca exceção.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Parâmetros de config do Redis que o painel pode ler (allowlist — nunca
# `CONFIG GET *`, que despeja tudo incl. `requirepass`).
CONFIG_ALLOWLIST = (
    "maxmemory", "maxmemory-policy", "appendonly", "save",
    "timeout", "tcp-keepalive", "databases",
)


def _redis():
    from src.infrastructure.redis_client import get_redis_text
    return get_redis_text()


def _redis_bytes():
    """Cliente sem decode — SLOWLOG/MODULE LIST podem carregar bytes binários
    que quebram o decode utf-8 do cliente de texto."""
    from src.infrastructure.redis_client import get_redis
    return get_redis()


def _s(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def redis_overview() -> dict:
    try:
        r = _redis()
        info = r.info()
        mem = r.info("memory")
        stats = r.info("stats")
        keyspace = r.info("keyspace") or {}
        total_keys = sum(
            int(v.get("keys", 0)) if isinstance(v, dict) else 0
            for v in keyspace.values()
        )
        hits = int(stats.get("keyspace_hits", 0))
        misses = int(stats.get("keyspace_misses", 0))
        hit_rate = round(100 * hits / (hits + misses), 1) if (hits + misses) else None
        return {
            "versao": info.get("redis_version"),
            "modo": info.get("redis_mode", "standalone"),
            "uptime_dias": round(int(info.get("uptime_in_seconds", 0)) / 86400, 1),
            "memoria_usada_mb": round(int(mem.get("used_memory", 0)) / 1048576, 1),
            "memoria_pico_mb": round(int(mem.get("used_memory_peak", 0)) / 1048576, 1),
            "maxmemory_mb": round(int(mem.get("maxmemory", 0)) / 1048576, 1) or None,
            "eviction_policy": mem.get("maxmemory_policy", "?"),
            "fragmentacao": round(float(mem.get("mem_fragmentation_ratio", 0)), 2),
            "clientes": int(info.get("connected_clients", 0)),
            "ops_por_seg": int(stats.get("instantaneous_ops_per_sec", 0)),
            "total_comandos": int(stats.get("total_commands_processed", 0)),
            "hit_rate": hit_rate,
            "keys": total_keys,
            "expired": int(stats.get("expired_keys", 0)),
            "evicted": int(stats.get("evicted_keys", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [STORAGE_HEALTH] redis_overview: %s", exc)
        return {"erro": str(exc)[:200]}


def redis_modules() -> list[dict]:
    try:
        raw = _redis_bytes().execute_command("MODULE", "LIST")
        mods = []
        for m in raw or []:
            if isinstance(m, dict):
                d = {_s(k): m[k] for k in m}
            else:
                d, it = {}, iter(m)
                for k in it:
                    d[_s(k)] = next(it, None)
            mods.append({"nome": _s(d.get("name", "?")), "versao": _s(d.get("ver", "?"))})
        return mods
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [STORAGE_HEALTH] redis_modules: %s", exc)
        return []


def redis_persistencia() -> dict:
    try:
        p = _redis().info("persistence")
        return {
            "aof_ativo": bool(int(p.get("aof_enabled", 0))),
            "rdb_ultimo_save_ok": bool(int(p.get("rdb_last_bgsave_status", "ok") == "ok")),
            "rdb_mudancas_desde_save": int(p.get("rdb_changes_since_last_save", 0)),
            "rdb_ultimo_save_epoch": int(p.get("rdb_last_save_time", 0)),
            "carregando": bool(int(p.get("loading", 0))),
        }
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)[:200]}


def redis_slowlog(n: int = 15) -> list[dict]:
    try:
        raw = _redis_bytes().execute_command("SLOWLOG", "GET", n)
        out = []
        for entry in raw or []:
            # [id, timestamp, micros, [args...], client_addr, client_name]
            args = entry[3] if len(entry) > 3 else []
            cmd = " ".join(_s(a) for a in args[:6])
            out.append({
                "id": int(entry[0]),
                "epoch": int(entry[1]),
                "ms": round(int(entry[2]) / 1000, 2),
                "comando": cmd[:120],
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [STORAGE_HEALTH] slowlog: %s", exc)
        return []


def redis_config() -> dict:
    try:
        r = _redis()
        out = {}
        for chave in CONFIG_ALLOWLIST:
            try:
                out[chave] = r.config_get(chave).get(chave, "")
            except Exception:  # noqa: BLE001
                out[chave] = "?"
        return out
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)[:200]}


async def postgres_overview() -> dict:
    from sqlalchemy import text
    from src.infrastructure.database.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as s:
            versao = (await s.execute(text("show server_version"))).scalar()
            db = (await s.execute(text("select current_database()"))).scalar()
            size = (await s.execute(text("select pg_size_pretty(pg_database_size(current_database()))"))).scalar()
            conns = (await s.execute(text(
                "select count(*), count(*) filter (where state='active') from pg_stat_activity"
            ))).one()
            max_conn = (await s.execute(text("show max_connections"))).scalar()
            slow = []
            try:
                rows = (await s.execute(text(
                    "select left(query, 90) q, calls, round(mean_exec_time::numeric,1) ms "
                    "from pg_stat_statements order by mean_exec_time desc limit 5"
                ))).all()
                slow = [{"query": r[0], "calls": r[1], "ms": float(r[2])} for r in rows]
            except Exception:  # noqa: BLE001 — extensão não instalada, ok
                slow = []
        return {
            "versao": versao, "database": db, "tamanho": size,
            "conexoes": conns[0], "conexoes_ativas": conns[1], "max_conexoes": int(max_conn),
            "queries_lentas": slow,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [STORAGE_HEALTH] postgres_overview: %s", exc)
        return {"erro": str(exc)[:200]}


def contar_por_prefixo(prefixo: str) -> int:
    """Conta chaves que casam `prefixo*` (usado pelo painel para mostrar o
    peso de cada namespace sem nada destrutivo)."""
    try:
        r, cur, total = _redis(), 0, 0
        while True:
            cur, keys = r.scan(cur, match=f"{prefixo}*", count=1000)
            total += len(keys)
            if cur == 0:
                break
        return total
    except Exception:  # noqa: BLE001
        return -1


async def recriar_indices() -> list[str]:
    """Recria os índices RediSearch de forma idempotente (se algum sumiu, ex.
    após um FLUSHDB acidental). NÃO restaura os documentos — só recria a
    estrutura vazia para o app parar de dar 'No such index'. A re-ingestão
    dos documentos é feita à parte."""
    from src.infrastructure.redis_client import inicializar_indices
    await inicializar_indices()
    return list(_redis().execute_command("FT._LIST") or [])
