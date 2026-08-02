"""
rest_lab/tools.py
=====================
Uma função async por operação REST. Retorno sempre `{"mensagem": str}` —
mesmo formato usado pelas tools reais do núcleo
(`src/capabilities/tools/tool_get_student_info.py`), pra já treinar o hábito
de devolver texto pronto pra virar mensagem de chat, não dado cru.

Erro de rede/HTTP nunca sobe como exceção pra fora daqui — cada função
captura `httpx.HTTPStatusError`/`httpx.RequestError` e devolve mensagem de
erro amigável, igual ao padrão defensivo de `tool_get_student_info.py`
(`ValueError` vira mensagem, não crash). É o router (`router.py`)/CLI
(`run_test.py`) que fica livre de try/except.
"""
from __future__ import annotations

import httpx

from rest_lab.clients import get_client

_LIMITE_LISTA = 10  # cap de itens numa lista formatada — pensando em WhatsApp


async def _get(nome_api: str, path: str, **kwargs) -> httpx.Response:
    client = get_client(nome_api)
    resp = await client.get(path, **kwargs)
    resp.raise_for_status()
    return resp


# ── JSONPlaceholder ─────────────────────────────────────────────────────────

async def listar_usuarios() -> dict:
    """GET /users — lista compacta (nome, email, cidade), até _LIMITE_LISTA."""
    try:
        resp = await _get("jsonplaceholder", "/users")
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro ao listar usuários: {e}"}

    usuarios = resp.json()[:_LIMITE_LISTA]
    linhas = [
        f"{u['id']}. {u['name']} — {u['email']} ({u['address']['city']})"
        for u in usuarios
    ]
    return {"mensagem": "👥 *Usuários (JSONPlaceholder)*\n" + "\n".join(linhas)}


async def obter_usuario(user_id: int) -> dict:
    """GET /users/{id} — detalhe de um usuário."""
    try:
        resp = await _get("jsonplaceholder", f"/users/{user_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"mensagem": f"❌ Usuário {user_id} não encontrado."}
        return {"mensagem": f"❌ Erro ao buscar usuário {user_id}: {e}"}
    except httpx.RequestError as e:
        return {"mensagem": f"❌ Erro ao buscar usuário {user_id}: {e}"}

    u = resp.json()
    return {
        "mensagem": (
            f"👤 *{u['name']}* (@{u['username']})\n"
            f"📧 {u['email']}\n"
            f"📞 {u['phone']}\n"
            f"🏢 {u['company']['name']}\n"
            f"📍 {u['address']['city']}"
        )
    }


async def criar_post(title: str, body: str) -> dict:
    """POST /posts — a API não persiste de verdade (comportamento documentado
    do JSONPlaceholder: sempre responde 201 com eco do payload + id fake)."""
    try:
        client = get_client("jsonplaceholder")
        resp = await client.post("/posts", json={"title": title, "body": body, "userId": 1})
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro ao criar post: {e}"}

    p = resp.json()
    return {"mensagem": f"✅ Post criado (id fake={p.get('id')}): \"{p.get('title')}\""}


async def atualizar_post(post_id: int, title: str) -> dict:
    """PUT /posts/{id} — mesma ressalva de não-persistência do criar_post."""
    try:
        client = get_client("jsonplaceholder")
        resp = await client.put(f"/posts/{post_id}", json={"id": post_id, "title": title, "body": "", "userId": 1})
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"mensagem": f"❌ Post {post_id} não encontrado."}
        return {"mensagem": f"❌ Erro ao atualizar post {post_id}: {e}"}
    except httpx.RequestError as e:
        return {"mensagem": f"❌ Erro ao atualizar post {post_id}: {e}"}

    p = resp.json()
    return {"mensagem": f"✅ Post {post_id} atualizado: \"{p.get('title')}\""}


async def deletar_post(post_id: int) -> dict:
    """DELETE /posts/{id} — JSONPlaceholder responde 200 mesmo pra ids
    inexistentes (não valida antes de "deletar"), então não há caso 404 aqui."""
    try:
        client = get_client("jsonplaceholder")
        resp = await client.delete(f"/posts/{post_id}")
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro ao deletar post {post_id}: {e}"}

    return {"mensagem": f"✅ Post {post_id} deletado."}


# ── DummyJSON ────────────────────────────────────────────────────────────────

async def listar_produtos(limit: int = _LIMITE_LISTA) -> dict:
    """GET /products?limit= — paginação real (total vem no payload)."""
    try:
        resp = await _get("dummyjson", "/products", params={"limit": limit})
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro ao listar produtos: {e}"}

    data = resp.json()
    linhas = [f"{p['id']}. {p['title']} — ${p['price']}" for p in data["products"]]
    return {
        "mensagem": (
            f"🛒 *Produtos (DummyJSON, {len(linhas)} de {data['total']})*\n"
            + "\n".join(linhas)
        )
    }


async def buscar_produto(nome: str) -> dict:
    """GET /products/search?q= — busca textual."""
    try:
        resp = await _get("dummyjson", "/products/search", params={"q": nome})
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro ao buscar produto '{nome}': {e}"}

    data = resp.json()
    if not data["products"]:
        return {"mensagem": f"🔍 Nenhum produto encontrado para '{nome}'."}
    linhas = [f"{p['id']}. {p['title']} — ${p['price']}" for p in data["products"][:_LIMITE_LISTA]]
    return {"mensagem": f"🔍 *Resultados para '{nome}'*\n" + "\n".join(linhas)}


# ── httpbin (debug puro de HTTP) ────────────────────────────────────────────

async def testar_status(code: int) -> dict:
    """GET /status/{code} — dispara o status code pedido, mostra o que voltou.
    Não usa `_get`/`raise_for_status` de propósito: o objetivo aqui é
    justamente observar status != 200 sem virar exceção."""
    try:
        client = get_client("httpbin")
        resp = await client.get(f"/status/{code}")
    except httpx.RequestError as e:
        return {"mensagem": f"❌ Erro de rede ao testar status {code}: {e}"}

    return {"mensagem": f"🔧 httpbin respondeu HTTP {resp.status_code} (pedido: {code})"}


async def echo_request() -> dict:
    """GET /get — mostra de volta os headers/args que o agente mandou."""
    try:
        resp = await _get("httpbin", "/get", params={"origem": "oraculo-rest-lab"})
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro no echo: {e}"}

    data = resp.json()
    return {
        "mensagem": (
            f"🔧 httpbin ecoou:\n"
            f"URL: {data['url']}\n"
            f"Args: {data['args']}\n"
            f"User-Agent: {data['headers'].get('User-Agent')}"
        )
    }
