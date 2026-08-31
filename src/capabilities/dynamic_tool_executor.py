"""
src/capabilities/dynamic_tool_executor.py — executa ferramentas do painel
========================================================================
Roda as ferramentas cadastradas em `tools_catalogo` (migration 016). É o
consumidor da parte "por dado" do catálogo de capabilities:
`capabilities/registry.py::executar_tool` cai aqui quando o nome pedido não
está no `_TOOL_REGISTRY` de código.

Tipos:
  - `http`: monta e dispara uma requisição REST. A URL é **revalidada**
    contra SSRF no momento da chamada (não só no cadastro) — a nota em
    `ssrf_validator.py` avisa que registro != conexão (DNS rebinding).
  - `mcp`: abre uma sessão MCP de vida curta contra a URL do servidor
    cadastrado em `mcp_servers` e chama a ferramenta remota.

Segredos: `config.auth` guarda só a REFERÊNCIA à env (`{"tipo":"bearer",
"env":"OPENAI_UEMA_KEY"}`), nunca o valor — o valor vem de `os.getenv` na
hora da chamada (decisão do dono: chave fica no `.env`, banco só aponta).

Toda falha vira `{"ok": False, "erro": "..."}` — nunca exceção não tratada
(mesmo contrato de `BaseNode.execute` e das tools de código).
"""
from __future__ import annotations

import json
import logging
import os
from string import Template
from typing import Any

import httpx

from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.security.ssrf_validator import URLInseguraError, validar_url_publica

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_MAX = 60.0


class ToolNaoEncontradaError(ValueError):
    """Nome pedido não está no catálogo `tools_catalogo` (nem no registro de
    código — checado por quem chama). Também levantada quando o Postgres está
    indisponível: sem conseguir confirmar a ferramenta, o seguro é tratar
    como inexistente, não executar às cegas."""


async def executar(nome: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Executa uma ferramenta do painel. Retorna `{"ok": bool, ...}` para
    resultado/falha de execução; levanta `ToolNaoEncontradaError` se o nome
    não existe no catálogo."""
    args = args or {}
    from src.capabilities import tool_catalog

    try:
        async with AsyncSessionLocal() as session:
            tool = await tool_catalog.obter_por_nome(session, nome)
    except Exception as exc:  # noqa: BLE001 — DB fora → não dá pra confirmar a tool
        raise ToolNaoEncontradaError(
            f"Não foi possível verificar a ferramenta '{nome}' (banco indisponível)."
        ) from exc

    if tool is None:
        raise ToolNaoEncontradaError(f"Ferramenta '{nome}' não existe.")
    if not tool["habilitado"]:
        return {"ok": False, "erro": f"Ferramenta '{nome}' está desligada."}

    try:
        if tool["tipo"] == "http":
            return await _executar_http(tool["config"], args)
        if tool["tipo"] == "mcp":
            return await _executar_mcp(tool["config"], args)
        return {"ok": False, "erro": f"Tipo desconhecido: {tool['tipo']}."}
    except URLInseguraError as exc:
        return {"ok": False, "erro": f"URL rejeitada por segurança: {exc}"}
    except Exception as exc:  # noqa: BLE001 — contrato: nunca propagar
        logger.warning("⚠️  [DYN_TOOL] '%s' falhou: %s", nome, exc)
        return {"ok": False, "erro": str(exc)[:300]}


# ── HTTP ────────────────────────────────────────────────────────────────────

def _render(template_str: str, args: dict) -> str:
    """Substitui `${chave}` pelos args. Placeholder ausente vira string vazia,
    não erro (a ferramenta pode ter args opcionais)."""
    if not template_str:
        return ""
    return Template(template_str).safe_substitute({k: str(v) for k, v in args.items()})


def _auth_headers(auth: dict) -> dict[str, str]:
    tipo = (auth or {}).get("tipo")
    env = (auth or {}).get("env")
    if not tipo or tipo in ("none", ""):
        return {}
    valor = os.getenv(env or "", "")
    if not valor:
        raise RuntimeError(f"Variável de ambiente '{env}' não definida para a autenticação.")
    if tipo == "bearer":
        return {"Authorization": f"Bearer {valor}"}
    if tipo == "api_key":
        header = (auth or {}).get("header") or "X-API-Key"
        return {header: valor}
    return {}


async def _executar_http(config: dict, args: dict) -> dict[str, Any]:
    url = _render(config["url"], args) or config["url"]
    validar_url_publica(url)  # revalida na conexão — não confiar só no cadastro

    headers = {**dict(config.get("headers") or {}), **_auth_headers(config.get("auth") or {})}
    for k, v in list(headers.items()):
        headers[k] = _render(str(v), args) if "${" in str(v) else str(v)

    corpo_raw = _render(config.get("corpo_template") or "", args)
    body_kwargs: dict[str, Any] = {}
    if corpo_raw.strip():
        try:
            body_kwargs["json"] = json.loads(corpo_raw)
        except json.JSONDecodeError:
            body_kwargs["content"] = corpo_raw

    timeout = min(float(config.get("timeout_s", 15) or 15), _HTTP_TIMEOUT_MAX)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.request(config.get("metodo", "GET"), url, headers=headers, **body_kwargs)

    texto = resp.text[:8000]
    try:
        dados = resp.json()
    except ValueError:
        dados = None

    return {
        "ok": resp.is_success,
        "status": resp.status_code,
        "dados": dados,
        "texto": None if dados is not None else texto,
    }


# ── MCP ─────────────────────────────────────────────────────────────────────

async def _mcp_server(nome: str) -> dict | None:
    """Registro de um servidor de `mcp_servers`, só se cadastrado E habilitado."""
    from src.graph import mcp_server_registry
    async with AsyncSessionLocal() as session:
        for s in await mcp_server_registry.listar(session):
            if s["name"] == nome and s["habilitado"]:
                return s
    return None


async def _executar_mcp(config: dict, args: dict) -> dict[str, Any]:
    import contextlib

    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from src.graph.mcp_server_registry import _auth_headers

    servidor = config["servidor"]
    reg = await _mcp_server(servidor)
    if reg is None:
        return {"ok": False, "erro": f"Servidor MCP '{servidor}' não está cadastrado ou está desligado."}

    url = reg["url"]
    validar_url_publica(url)  # revalida na conexão
    headers = _auth_headers(reg.get("auth_tipo", "none"), reg.get("auth_env", ""))

    call_args = {**(config.get("args_template") or {}), **args}
    async with contextlib.AsyncExitStack() as stack:
        http_client = await stack.enter_async_context(create_mcp_http_client(headers=headers)) if headers else None
        read, write = await stack.enter_async_context(streamable_http_client(url, http_client=http_client))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        resultado = await session.call_tool(config["tool_remota"], call_args)

    partes = []
    for item in getattr(resultado, "content", []) or []:
        partes.append(getattr(item, "text", None) or str(item))
    return {"ok": not getattr(resultado, "isError", False), "conteudo": "\n".join(partes)}
