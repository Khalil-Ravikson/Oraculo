"""
src/graph/topology_registry.py — persistência de topologias de grafo
======================================================================
Lê/escreve `graph_topology` (migration 015). Toda escrita passa por
`topology_validator.validar_topologia()` ANTES do INSERT/UPDATE — uma
topologia inválida (nó fantasma, tipo incompatível, ciclo) nunca chega a
ser persistida, o admin vê os erros e corrige no canvas antes de salvar
de novo.

Sem execução real ainda — salvar uma topologia aqui não afeta nenhum
fluxo de produção (mesmo estágio de `mcp_server_registry.py`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.database.models import GraphTopology
from src.graph_studio.node_registry import get_registry
from src.graph_studio.topology_validator import validar_topologia

logger = logging.getLogger(__name__)


STATUS_VALIDOS = ("draft", "testado", "publicado")


class TopologiaInvalidaError(ValueError):
    """Topologia não passou na validação — `erros` tem a lista completa."""

    def __init__(self, erros: list[str]):
        self.erros = erros
        super().__init__("; ".join(erros))


async def salvar(
    session, name: str, topology_json: Dict[str, Any],
    description: str = "", status: str = "draft", admin: str | None = None,
    gatilho: str | None = None,
) -> dict:
    """
    Valida e persiste (cria ou atualiza, por `name`). Levanta
    `TopologiaInvalidaError` se a validação falhar — nunca escreve uma
    topologia inválida no banco.
    """
    erros = validar_topologia(topology_json, get_registry())
    if erros:
        raise TopologiaInvalidaError(erros)

    if status not in STATUS_VALIDOS:
        status = "draft"
    gatilho = (gatilho or "").strip()[:200] or None

    stmt = pg_insert(GraphTopology).values(
        name=name, description=description, topology_json=topology_json,
        status=status, gatilho=gatilho, atualizado_por=admin,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "name"],
        set_={
            "description": description,
            "topology_json": topology_json,
            "status": status,
            "gatilho": gatilho,
            "atualizado_por": admin,
            "atualizado_em": datetime.now(timezone.utc),
            "versao": GraphTopology.versao + 1,
        },
    )
    await session.execute(stmt)
    await session.flush()

    linha = await obter(session, name)
    return linha


async def listar(session) -> list[dict]:
    try:
        result = await session.execute(
            select(GraphTopology).where(GraphTopology.tenant_id.is_(None))
            .order_by(GraphTopology.name)
        )
        return [_serializar(r) for r in result.scalars().all()]
    except Exception as exc:
        logger.warning("⚠️  [TOPOLOGY_REGISTRY] Falha ao listar: %s", exc)
        return []


async def obter(session, name: str) -> dict | None:
    result = await session.execute(
        select(GraphTopology).where(
            GraphTopology.name == name, GraphTopology.tenant_id.is_(None)
        )
    )
    linha = result.scalar_one_or_none()
    return _serializar(linha) if linha else None


async def remover(session, name: str) -> bool:
    res = await session.execute(
        delete(GraphTopology).where(
            GraphTopology.name == name, GraphTopology.tenant_id.is_(None)
        )
    )
    await session.flush()
    return res.rowcount > 0


def _serializar(r: GraphTopology) -> dict:
    return {
        "name": r.name, "description": r.description,
        "topology_json": r.topology_json, "status": r.status,
        "gatilho": r.gatilho,
        "versao": r.versao, "atualizado_em": r.atualizado_em,
        "atualizado_por": r.atualizado_por,
    }
