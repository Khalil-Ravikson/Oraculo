"""
src/infrastructure/observability/system_health.py — visão única de saúde (Hub v2 Sprint 7)
========================================================================================
Agrega, num só lugar, o que o operador precisa checar quando "algo está
estranho": provedores de LLM (disjuntor), componentes do grafo, servidores
MCP, bancos, filas Celery, e as flags de laboratório ativas.

Cada bloco é fail-safe: uma falha vira `{"estado": "erro", ...}`, nunca
derruba o painel.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_ESTADO_CIRCUITO = {"fechado": "operacional", "meio_aberto": "testando recuperação", "aberto": "bloqueado por falhas"}


async def coletar() -> dict:
    # `_provedores`/`_componentes`/`_filas` fazem I/O síncrono (Redis, broadcast
    # Celery) — rodam em thread pool pra não travar o event loop da API
    # (`_filas` sozinho segura o loop por até 3s no `control.ping`).
    provedores, componentes, filas = await asyncio.gather(
        asyncio.to_thread(_provedores),
        asyncio.to_thread(_componentes),
        asyncio.to_thread(_filas),
    )
    return {
        "provedores": provedores,
        "componentes": componentes,
        "mcp": await _mcp(),
        "bancos": await _bancos(),
        "filas": filas,
        "flags_laboratorio": _flags(),
    }


def _provedores() -> list[dict]:
    try:
        from src.infrastructure.adapters import llm_circuit_breaker, llm_provider_registry
        from src.infrastructure.adapters.llm_factory import _provider_global_ativo
        ativo = _provider_global_ativo()
        out = []
        for nome in llm_provider_registry.registrados():
            estado = llm_circuit_breaker.estado(nome)
            saude = llm_provider_registry.health_check(nome)
            out.append({
                "nome": nome,
                "ativo": nome == ativo,
                "credencial": saude,
                "circuito": _ESTADO_CIRCUITO.get(estado, estado),
                "circuito_raw": estado,
                "falhas": llm_circuit_breaker._falhas(nome),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [SYSTEM_HEALTH] provedores: %s", exc)
        return []


def _componentes() -> list[dict]:
    try:
        from src.graph import node_health
        from src.graph.node_registry import get_registry
        out = []
        for n in get_registry().list_nodes():
            s = node_health.resolver(n["type"])
            out.append({
                "id": n["id"], "tipo": n["type"],
                "estado": ("operacional" if s and s["is_healthy"]
                           else "com erro" if s and not s["is_healthy"]
                           else "não monitorado"),
                "detalhe": (s.get("error") or s.get("detail")) if s else None,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [SYSTEM_HEALTH] componentes: %s", exc)
        return []


async def _mcp() -> list[dict]:
    try:
        from src.graph import mcp_server_registry
        from src.infrastructure.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            servidores = await mcp_server_registry.listar(s)
        return [
            {
                "nome": sv["name"], "habilitado": sv["habilitado"],
                "latency_ms": sv["latency_ms"], "last_checked": sv["last_checked"],
                "ferramentas": len(sv.get("tools_expostas") or []),
            }
            for sv in servidores
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [SYSTEM_HEALTH] mcp: %s", exc)
        return []


async def _bancos() -> dict:
    from src.infrastructure.observability import storage_health
    redis = storage_health.redis_overview()
    pg = await storage_health.postgres_overview()
    return {
        "cache": {
            "estado": "erro" if redis.get("erro") else "operacional",
            "memoria_mb": redis.get("memoria_usada_mb"),
            "hit_rate": redis.get("hit_rate"),
            "clientes": redis.get("clientes"),
        },
        "banco": {
            "estado": "erro" if pg.get("erro") else "operacional",
            "conexoes": pg.get("conexoes"),
            "max_conexoes": pg.get("max_conexoes"),
            "tamanho": pg.get("tamanho"),
        },
    }


def _filas() -> dict:
    try:
        from src.infrastructure.celery_app import celery_app
        # `ping` volta como lista de pacotes [{worker: {...}}, ...] — cada
        # worker pode responder num pacote separado, então junta todos.
        result = celery_app.control.ping(timeout=3) or []
        workers = sorted({nome for pacote in result for nome in pacote})
        return {"estado": "operacional" if workers else "sem resposta", "workers": workers}
    except Exception as exc:  # noqa: BLE001
        return {"estado": "erro", "erro": str(exc)[:150], "workers": []}


def _flags() -> list[dict]:
    try:
        from src.infrastructure import dynamic_config
        from src.infrastructure.dynamic_config import ALLOWED_DYNAMIC_KEYS
        out = []
        for chave, tipo in ALLOWED_DYNAMIC_KEYS.items():
            if not (chave.startswith("FEATURE_") or chave.startswith("DEV_TEST_")):
                continue
            valor = dynamic_config.get_bool(chave) if tipo == "bool" else dynamic_config.get_str(chave)
            out.append({"chave": chave, "ativa": bool(valor)})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [SYSTEM_HEALTH] flags: %s", exc)
        return []
