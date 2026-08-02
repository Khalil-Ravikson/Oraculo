"""
rest_lab/clients.py
=======================
Um `httpx.AsyncClient` por API, como singleton lazy (mesmo espírito do
`_get_client()` em `src/router/llm_fallback.py`) — evita reabrir conexão TCP
a cada chamada dentro da mesma sessão de CLI. `timeout=10` porque são APIs
públicas de terceiros: se demorar mais que isso, a tool deve falhar rápido
e devolver mensagem de erro em vez de travar o loop do `run_test.py`.
"""
from __future__ import annotations

import httpx

_clients: dict[str, httpx.AsyncClient] = {}

_BASE_URLS = {
    "jsonplaceholder": "https://jsonplaceholder.typicode.com",
    "dummyjson": "https://dummyjson.com",
    "httpbin": "https://httpbin.org",
}


def get_client(nome: str) -> httpx.AsyncClient:
    if nome not in _clients:
        _clients[nome] = httpx.AsyncClient(base_url=_BASE_URLS[nome], timeout=10)
    return _clients[nome]


async def fechar_todos() -> None:
    """Chamado só na saída do CLI (`run_test.py`) — fecha as conexões abertas."""
    for client in _clients.values():
        await client.aclose()
    _clients.clear()
