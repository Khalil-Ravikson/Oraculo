"""
src/graph/mcp_server_registry.py — cadastro admin de servidores MCP
======================================================================
Lê/escreve `mcp_servers` (migrations 014 + 019). Primeira peça do "MCP
Connection Manager" da Fase 8.

Sprint 4 (Hub v2): além do cadastro, agora há conexão de verdade sob demanda
— `testar_conexao()` mede latência e lista ferramentas; `sincronizar_
ferramentas()` insere as ferramentas do servidor em `tools_catalogo` como
tipo `mcp` (ficam disponíveis para vincular a um agente em /hub/capabilities).

Toda `url` passa por `ssrf_validator.validar_url_publica()` no cadastro E
antes de cada conexão (a nota em `ssrf_validator.py` avisa que registro !=
conexão — DNS rebinding). Autenticação: `auth_tipo` (none|bearer|api_key) +
`auth_env` = NOME da variável de ambiente com o segredo (nunca o valor).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.models import McpServer
from src.infrastructure.security.ssrf_validator import URLInseguraError, validar_url_publica

logger = logging.getLogger(__name__)

_AUTH_VALIDOS = ("none", "bearer", "api_key")


class NomeDuplicadoError(ValueError):
    """Já existe um servidor MCP registrado com esse `name`."""


async def registrar(
    session, name: str, url: str, description: str = "", *,
    auth_tipo: str = "none", auth_env: str = "", admin: str | None = None,
) -> dict:
    validar_url_publica(url)  # levanta URLInseguraError
    if auth_tipo not in _AUTH_VALIDOS:
        raise ValueError(f"Autenticação inválida: {auth_tipo}.")

    registro = McpServer(
        name=name, url=url, description=description,
        auth_tipo=auth_tipo, auth_env=auth_env.strip(), atualizado_por=admin,
    )
    session.add(registro)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise NomeDuplicadoError(f"Já existe um servidor MCP chamado '{name}'.") from exc

    return _row_dict(registro)


async def listar(session) -> list[dict]:
    try:
        result = await session.execute(
            select(McpServer).where(McpServer.tenant_id.is_(None)).order_by(McpServer.name)
        )
        return [_row_dict(r) for r in result.scalars().all()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [MCP_SERVER_REGISTRY] Falha ao listar: %s", exc)
        return []


async def obter(session, name: str) -> dict | None:
    r = (await session.execute(
        select(McpServer).where(McpServer.name == name, McpServer.tenant_id.is_(None))
    )).scalar_one_or_none()
    return _row_dict(r) if r else None


async def set_habilitado(session, name: str, habilitado: bool, admin: str | None = None) -> bool:
    res = await session.execute(
        update(McpServer)
        .where(McpServer.name == name, McpServer.tenant_id.is_(None))
        .values(habilitado=habilitado, atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc), versao=McpServer.versao + 1)
    )
    await session.flush()
    return res.rowcount > 0


async def remover(session, name: str) -> bool:
    res = await session.execute(
        delete(McpServer).where(McpServer.name == name, McpServer.tenant_id.is_(None))
    )
    await session.flush()
    return res.rowcount > 0


# ── Conexão real (sob demanda) ────────────────────────────────────────────

def _auth_headers(auth_tipo: str, auth_env: str) -> dict:
    if auth_tipo == "none" or not auth_env:
        return {}
    valor = os.getenv(auth_env, "")
    if not valor:
        return {}
    if auth_tipo == "bearer":
        return {"Authorization": f"Bearer {valor}"}
    return {"X-API-Key": valor}


async def _listar_tools_remotas(url: str, auth_tipo: str, auth_env: str) -> tuple[float, list[dict]]:
    """Abre uma sessão MCP de vida curta, mede latência do handshake e lista
    as ferramentas. Levanta em qualquer falha de conexão."""
    import contextlib

    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    validar_url_publica(url)  # revalida na conexão
    headers = _auth_headers(auth_tipo, auth_env)
    t0 = time.monotonic()
    async with contextlib.AsyncExitStack() as stack:
        http_client = None
        if headers:
            http_client = await stack.enter_async_context(create_mcp_http_client(headers=headers))
        read, write = await stack.enter_async_context(streamable_http_client(url, http_client=http_client))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        latency = (time.monotonic() - t0) * 1000
        resp = await session.list_tools()
        tools = [
            {"nome": t.name, "descricao": getattr(t, "description", "") or ""}
            for t in getattr(resp, "tools", []) or []
        ]
    return latency, tools


async def testar_conexao(session, name: str) -> dict:
    """Conecta, mede latência, lista ferramentas e grava o resultado na
    linha. Retorna `{"ok": bool, "latency_ms": int, "tools": [...], "erro": ...}`."""
    canal = await obter(session, name)
    if canal is None:
        return {"ok": False, "erro": "Servidor não encontrado."}
    try:
        latency, tools = await _listar_tools_remotas(canal["url"], canal["auth_tipo"], canal["auth_env"])
    except URLInseguraError as exc:
        return {"ok": False, "erro": f"URL rejeitada: {exc}"}
    except Exception as exc:  # noqa: BLE001
        await session.execute(
            update(McpServer).where(McpServer.name == name, McpServer.tenant_id.is_(None))
            .values(last_checked=datetime.now(timezone.utc), latency_ms=None)
        )
        await session.flush()
        return {"ok": False, "erro": str(exc)[:200]}

    await session.execute(
        update(McpServer).where(McpServer.name == name, McpServer.tenant_id.is_(None))
        .values(latency_ms=int(latency), last_checked=datetime.now(timezone.utc), tools_expostas=tools)
    )
    await session.flush()
    return {"ok": True, "latency_ms": int(latency), "tools": tools}


async def sincronizar_ferramentas(session, name: str) -> dict:
    """Testa a conexão e insere/atualiza as ferramentas do servidor em
    `tools_catalogo` (tipo `mcp`). Retorna quantas ferramentas processou."""
    resultado = await testar_conexao(session, name)
    if not resultado["ok"]:
        return resultado

    from src.capabilities import tool_catalog

    criadas = 0
    for t in resultado["tools"]:
        nome_local = f"{name}_{t['nome']}"
        if await tool_catalog.obter_por_nome(session, nome_local):
            continue
        try:
            await tool_catalog.criar(
                session, nome_local, "mcp",
                {"servidor": name, "tool_remota": t["nome"]},
                descricao=t["descricao"] or f"{t['nome']} (via {name})",
                admin="mcp-sync",
            )
            criadas += 1
        except (tool_catalog.NomeDuplicadoError, tool_catalog.ConfigInvalidaError):
            continue
    return {"ok": True, "total": len(resultado["tools"]), "criadas": criadas, "latency_ms": resultado["latency_ms"]}


def _row_dict(r: McpServer) -> dict:
    return {
        "name": r.name, "url": r.url, "description": r.description,
        "habilitado": r.habilitado, "versao": r.versao,
        "auth_tipo": r.auth_tipo, "auth_env": r.auth_env,
        "latency_ms": r.latency_ms,
        "last_checked": r.last_checked.isoformat() if r.last_checked else None,
        "tools_expostas": r.tools_expostas or [],
        "atualizado_em": r.atualizado_em, "atualizado_por": r.atualizado_por,
    }
