"""
src/graph/mcp_server_registry.py — cadastro admin de servidores MCP
======================================================================
Lê/escreve `mcp_servers` (migration 014). Primeira peça do "MCP Connection
Manager" da Fase 8 — hoje só o cadastro (Configuration Layer, dado puro),
sem conexão real ainda (`mcp_lab/clients.py` continua com as 3 URLs
hardcoded do gateway pipeworx). Registrar aqui não altera o que o sistema
de fato chama — é preparação de dado pra quando essa integração existir.

Toda `url` passa por `ssrf_validator.validar_url_publica()` ANTES de
qualquer escrita — obrigatório desde o primeiro commit (docs/historico/
plataforma_orientada_a_configuracao.md §G: "SSRF continua o risco
concreto mais alto do roadmap"), não um passo opcional.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.models import McpServer
from src.infrastructure.security.ssrf_validator import validar_url_publica, URLInseguraError

logger = logging.getLogger(__name__)


class NomeDuplicadoError(ValueError):
    """Já existe um servidor MCP registrado com esse `name`."""


async def registrar(
    session, name: str, url: str, description: str = "", admin: str | None = None
) -> dict:
    """
    Cadastra um novo servidor MCP. Levanta `URLInseguraError` se a URL
    apontar (ou resolver) para rede privada/reservada, `NomeDuplicadoError`
    se `name` já existe. Nunca faz upsert silencioso — registrar um nome
    já usado é erro do admin, não sobrescrita implícita.
    """
    validar_url_publica(url)  # levanta URLInseguraError — propositalmente não capturado aqui

    registro = McpServer(name=name, url=url, description=description, atualizado_por=admin)
    session.add(registro)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise NomeDuplicadoError(f"Já existe um servidor MCP chamado '{name}'.") from exc

    return {
        "name": registro.name, "url": registro.url, "description": registro.description,
        "habilitado": registro.habilitado, "versao": registro.versao,
    }


async def listar(session) -> list[dict]:
    try:
        result = await session.execute(
            select(McpServer).where(McpServer.tenant_id.is_(None)).order_by(McpServer.name)
        )
        return [
            {
                "name": r.name, "url": r.url, "description": r.description,
                "habilitado": r.habilitado, "versao": r.versao,
                "atualizado_em": r.atualizado_em, "atualizado_por": r.atualizado_por,
            }
            for r in result.scalars().all()
        ]
    except Exception as exc:
        logger.warning("⚠️  [MCP_SERVER_REGISTRY] Falha ao listar: %s", exc)
        return []


async def set_habilitado(session, name: str, habilitado: bool, admin: str | None = None) -> bool:
    """Liga/desliga um servidor já cadastrado. Retorna False se `name` não existe."""
    res = await session.execute(
        update(McpServer)
        .where(McpServer.name == name, McpServer.tenant_id.is_(None))
        .values(habilitado=habilitado, atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc), versao=McpServer.versao + 1)
    )
    await session.flush()
    return res.rowcount > 0


async def remover(session, name: str) -> bool:
    """Remove um servidor cadastrado. Retorna False se `name` não existe."""
    res = await session.execute(
        delete(McpServer).where(McpServer.name == name, McpServer.tenant_id.is_(None))
    )
    await session.flush()
    return res.rowcount > 0
