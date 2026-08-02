"""
mcp_lab/clients.py
=====================
Sessão MCP de vida curta — abre, usa, fecha a cada chamada. Diferente do
`get_client()` singleton de `rest_lab/clients.py` (que mantém um
`httpx.AsyncClient` vivo pra reusar conexão TCP): aqui a decisão é
deliberada (ver plano da sessão) — sem estado persistente entre chamadas,
menor pegada de RAM, e evita reintroduzir a classe de bug de
event-loop-persistente-por-worker já sofrida com `AsyncRedisSaver`
(`.claude.md`, seção LangGraph + Celery). O custo é reabrir handshake MCP
(`initialize()`) a cada chamada — aceitável pro volume de um laboratório de
estudo/comando avulso no WhatsApp, não pra alto throughput.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

_GATEWAY = "https://gateway.pipeworx.io"


@asynccontextmanager
async def _pipeworx_session(pack: str):
    """Sessão MCP de vida curta contra um pack hospedado do gateway pipeworx
    (`gateway.pipeworx.io/<pack>/mcp`)."""
    url = f"{_GATEWAY}/{pack}/mcp"
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def stackexchange_session():
    return _pipeworx_session("stackexchange")


def brave_session():
    return _pipeworx_session("brave-search")


def github_session():
    return _pipeworx_session("github")
