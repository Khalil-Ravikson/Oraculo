"""
src/graph/node_config.py — habilitar/desabilitar nós do NodeRegistry
======================================================================
Lê/escreve `graph_node_config` (migration 013). Mesma filosofia de
`capabilities/agent_tools.py`: o código (`src/graph/nodes/`) decide QUAIS
nós existem; o admin só liga/desliga via /hub/graph-nodes. Um `node_id`
sem linha na tabela é implicitamente habilitado.

Degrada sem exceção — falha de Postgres → todos os nós aparecem
habilitados (fail-open, mesmo padrão de `dynamic_config`/`pricing.py`:
nunca deixar uma falha de infra esconder um nó que na verdade funciona).

`config_overrides` (JSONB) existe no schema mas ainda não tem consumidor —
nenhum nó define `config_schema` não-vazio hoje (todos herdam o default
`{}` de `BaseNode`), então não há o que validar/editar ainda. Fica pronto
pro dia em que um nó (ex.: LLMNode com `model`/`temperatura` configuráveis)
precisar disso — não implementar a UI de edição antes de existir um schema
real seria construir formulário para nada.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.database.models import GraphNodeConfig

logger = logging.getLogger(__name__)


async def listar(session) -> list[dict]:
    """Todas as linhas de config explícita (só os nós que já foram
    tocados por um admin — nós nunca mexidos não aparecem aqui, ver
    `mesclar_com_registry`)."""
    try:
        result = await session.execute(
            select(GraphNodeConfig).where(GraphNodeConfig.tenant_id.is_(None))
            .order_by(GraphNodeConfig.node_id)
        )
        return [
            {
                "node_id": r.node_id, "habilitado": r.habilitado,
                "config_overrides": r.config_overrides, "versao": r.versao,
                "atualizado_em": r.atualizado_em, "atualizado_por": r.atualizado_por,
            }
            for r in result.scalars().all()
        ]
    except Exception as exc:
        logger.warning("⚠️  [GRAPH_NODE_CONFIG] Falha ao listar: %s", exc)
        return []


def mesclar_com_registry(nos_registry: list[dict], config_rows: list[dict]) -> list[dict]:
    """Une a lista de nós do NodeRegistry (Registry Layer, sempre presente)
    com as linhas de config explícita (Configuration Layer, só existe pra
    quem já foi tocado). Nó sem linha = habilitado=True, versao=0 (mesma
    convenção de `config_dinamica.snapshot`: versao 0 = "nunca gravado")."""
    por_node_id = {row["node_id"]: row for row in config_rows}
    saida = []
    for no in nos_registry:
        cfg = por_node_id.get(no["id"])
        saida.append({
            **no,
            "habilitado": cfg["habilitado"] if cfg else True,
            "config_overrides": cfg["config_overrides"] if cfg else {},
            "versao": cfg["versao"] if cfg else 0,
            "atualizado_em": cfg["atualizado_em"].isoformat() if cfg and cfg["atualizado_em"] else None,
            "atualizado_por": cfg["atualizado_por"] if cfg else None,
        })
    return saida


async def set_habilitado(session, node_id: str, habilitado: bool, admin: str | None = None) -> None:
    """Liga/desliga um nó. Cria a linha se ainda não existir (nó nunca
    tocado por admin) — diferente de `agent_tools.set_habilitado`, que
    espera um binding pré-existente (semeado por `upsert_binding_from_code`);
    aqui não há seed algum, então o primeiro toggle É a criação."""
    stmt = pg_insert(GraphNodeConfig).values(
        node_id=node_id, habilitado=habilitado, atualizado_por=admin,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "node_id"],
        set_={
            "habilitado": habilitado,
            "atualizado_por": admin,
            "atualizado_em": datetime.now(timezone.utc),
            "versao": GraphNodeConfig.versao + 1,
        },
    )
    await session.execute(stmt)
    await session.flush()
