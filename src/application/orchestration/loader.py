"""
src/application/orchestration/loader.py
======================================
Carrega a `GraphSpec` ATIVA — a topologia que o grafo de produção usa.

Mesma mecânica de `dynamic_config.py` / `route_registry.py`:

    Redis (espelho de leitura rápida)  →  Postgres (`graph_spec`)  →  specs/default.json

`carregar_spec_ativa()` é SÍNCRONO (roda dentro de `builder.build_graph()`,
que roda no boot do grafo por processo). Nunca levanta por falta de infra —
cai no `default.json` embutido (fallback de desastre). Levanta só se a spec
efetivamente carregada for inválida (topologia quebrada = melhor falhar no
boot do que rotear errado em produção).

`hydrate_redis()` roda no startup do FastAPI e no `worker_process_init` do
Celery — idêntico ao que os outros registries já fazem.
"""
from __future__ import annotations

import json
import logging
import pathlib

from src.application.orchestration.spec import GraphSpec, spec_valida_ou_erro

logger = logging.getLogger(__name__)

_REDIS_KEY = "graph:spec:ativa"
_DEFAULT_PATH = pathlib.Path(__file__).parent / "specs" / "default.json"


def _default_raw() -> dict:
    return json.loads(_DEFAULT_PATH.read_text(encoding="utf-8"))


def default_spec() -> GraphSpec:
    """A spec embutida (`specs/default.json`). Validada — um default quebrado
    é bug de código, não de dado."""
    return spec_valida_ou_erro(_default_raw())


def _ler_redis() -> dict | None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        raw = get_redis_text().get(_REDIS_KEY)
        if not raw:
            return None
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [GRAPH_SPEC] Falha ao ler spec no Redis: %s", exc)
        return None


def _ler_postgres_sync() -> dict | None:
    """Postgres via engine síncrona derivada da `DATABASE_URL` (o builder roda
    fora de event loop no boot). Espelha o padrão de leitura síncrona de
    `route_registry`; melhor-esforço — cai pro default se não der."""
    try:
        from sqlalchemy import create_engine, text
        from src.infrastructure.settings import settings

        url = settings.DATABASE_URL.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT spec FROM graph_spec WHERE tenant_id IS NULL "
                    "ORDER BY versao DESC LIMIT 1"
                )).first()
        finally:
            engine.dispose()
        if row and row[0]:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [GRAPH_SPEC] Falha ao ler spec no Postgres: %s", exc)
        return None


def carregar_spec_ativa() -> GraphSpec:
    raw = _ler_redis()
    fonte = "redis"
    if raw is None:
        raw = _ler_postgres_sync()
        fonte = "postgres"
        if raw is not None:
            _espelhar_redis(raw)
    if raw is None:
        raw = _default_raw()
        fonte = "default.json"

    try:
        spec = spec_valida_ou_erro(raw)
    except ValueError:
        if fonte == "default.json":
            raise
        logger.error("❌ [GRAPH_SPEC] Spec de %s inválida — caindo no default.json.", fonte)
        return default_spec()

    logger.info("🧭 [GRAPH_SPEC] Topologia carregada de %s (v%s, %d nós).",
                fonte, spec.version, len(spec.nodes))
    return spec


def _espelhar_redis(raw: dict) -> None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().set(_REDIS_KEY, json.dumps(raw, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [GRAPH_SPEC] Falha ao espelhar spec no Redis: %s", exc)


async def hydrate_redis() -> bool:
    """Lê a spec ativa do Postgres e espelha no Redis. Boot do FastAPI e de
    cada worker Celery."""
    try:
        from sqlalchemy import text
        from src.infrastructure.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            row = (await session.execute(text(
                "SELECT spec FROM graph_spec WHERE tenant_id IS NULL "
                "ORDER BY versao DESC LIMIT 1"
            ))).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [GRAPH_SPEC] hydrate_redis falhou ao ler Postgres: %s", exc)
        return False

    if not row or not row[0]:
        # Nenhuma spec editada no Hub ainda — espelha o default embutido, pra
        # `carregar_spec_ativa()` sempre bater no Redis (sem connect síncrono
        # ao Postgres no boot de cada processo).
        _espelhar_redis(_default_raw())
        logger.info("🧭 [GRAPH_SPEC] Sem spec no Postgres — default.json espelhado no Redis.")
        return False

    raw = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    _espelhar_redis(raw)
    logger.info("🧭 [GRAPH_SPEC] Spec espelhada no Redis a partir do Postgres.")
    return True
