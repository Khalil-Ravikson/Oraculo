"""
src/application/use_cases/rest_lab_use_case.py
=================================================
Fase 3 do plano de integração LangGraph/REST/MCP (Decisão 03): camada de
Application que `rest_lab/` não tinha — antes desta fase, `rest_lab/tools.py`
chamava `httpx` direto via `rest_lab/clients.py`, sem passar por nenhum caso
de uso, inconsistente com o resto do projeto (`src/application/use_cases/`).

`rest_lab/` continua sendo o que sempre foi: um laboratório de estudo de
integração REST contra APIs públicas de terceiros (JSONPlaceholder,
DummyJSON, httpbin) — não é uma API real da UEMA, não toca banco/Redis/
infraestrutura de produção. Mover a lógica pra cá não muda esse fato, só
corrige a camada: `rest_lab/router.py` (regex de comando) → `rest_lab/tools.py`
(facade fino, mantém a assinatura/contrato que já existia) →
`RestLabUseCase` (aqui, orquestra) → `rest_lab/clients.py` (infraestrutura,
httpx). Comportamento idêntico ao pré-Fase 3 — só a camada muda, não o
resultado (ver testes em tests/unit/application/test_rest_lab_use_case.py).
"""
from __future__ import annotations

import httpx

from rest_lab.clients import get_client

_LIMITE_LISTA = 10  # cap de itens numa lista formatada — pensando em WhatsApp


class RestLabUseCase:
    """Um método por operação REST do laboratório — mesmo contrato de
    retorno que `rest_lab/tools.py` sempre teve: `{"mensagem": str}`, erro de
    rede/HTTP nunca sobe como exceção (capturado aqui, não no chamador)."""

    async def _get(self, nome_api: str, path: str, **kwargs) -> httpx.Response:
        client = get_client(nome_api)
        resp = await client.get(path, **kwargs)
        resp.raise_for_status()
        return resp

    # ── JSONPlaceholder ─────────────────────────────────────────────────────

    async def listar_usuarios(self) -> dict:
        """GET /users — lista compacta (nome, email, cidade), até _LIMITE_LISTA."""
        try:
            resp = await self._get("jsonplaceholder", "/users")
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            return {"mensagem": f"❌ Erro ao listar usuários: {e}"}

        usuarios = resp.json()[:_LIMITE_LISTA]
        linhas = [
            f"{u['id']}. {u['name']} — {u['email']} ({u['address']['city']})"
            for u in usuarios
        ]
        return {"mensagem": "👥 *Usuários (JSONPlaceholder)*\n" + "\n".join(linhas)}

    async def obter_usuario(self, user_id: int) -> dict:
        """GET /users/{id} — detalhe de um usuário."""
        try:
            resp = await self._get("jsonplaceholder", f"/users/{user_id}")
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

    async def criar_post(self, title: str, body: str) -> dict:
        """POST /posts — a API não persiste de verdade (comportamento
        documentado do JSONPlaceholder: sempre responde 201 com eco do
        payload + id fake)."""
        try:
            client = get_client("jsonplaceholder")
            resp = await client.post("/posts", json={"title": title, "body": body, "userId": 1})
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            return {"mensagem": f"❌ Erro ao criar post: {e}"}

        p = resp.json()
        return {"mensagem": f"✅ Post criado (id fake={p.get('id')}): \"{p.get('title')}\""}

    async def atualizar_post(self, post_id: int, title: str) -> dict:
        """PUT /posts/{id} — mesma ressalva de não-persistência do criar_post."""
        try:
            client = get_client("jsonplaceholder")
            resp = await client.put(
                f"/posts/{post_id}", json={"id": post_id, "title": title, "body": "", "userId": 1},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"mensagem": f"❌ Post {post_id} não encontrado."}
            return {"mensagem": f"❌ Erro ao atualizar post {post_id}: {e}"}
        except httpx.RequestError as e:
            return {"mensagem": f"❌ Erro ao atualizar post {post_id}: {e}"}

        p = resp.json()
        return {"mensagem": f"✅ Post {post_id} atualizado: \"{p.get('title')}\""}

    async def deletar_post(self, post_id: int) -> dict:
        """DELETE /posts/{id} — JSONPlaceholder responde 200 mesmo pra ids
        inexistentes (não valida antes de "deletar"), então não há caso 404
        aqui."""
        try:
            client = get_client("jsonplaceholder")
            resp = await client.delete(f"/posts/{post_id}")
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            return {"mensagem": f"❌ Erro ao deletar post {post_id}: {e}"}

        return {"mensagem": f"✅ Post {post_id} deletado."}

    # ── DummyJSON ────────────────────────────────────────────────────────────

    async def listar_produtos(self, limit: int = _LIMITE_LISTA) -> dict:
        """GET /products?limit= — paginação real (total vem no payload)."""
        try:
            resp = await self._get("dummyjson", "/products", params={"limit": limit})
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

    async def buscar_produto(self, nome: str) -> dict:
        """GET /products/search?q= — busca textual."""
        try:
            resp = await self._get("dummyjson", "/products/search", params={"q": nome})
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            return {"mensagem": f"❌ Erro ao buscar produto '{nome}': {e}"}

        data = resp.json()
        if not data["products"]:
            return {"mensagem": f"🔍 Nenhum produto encontrado para '{nome}'."}
        linhas = [f"{p['id']}. {p['title']} — ${p['price']}" for p in data["products"][:_LIMITE_LISTA]]
        return {"mensagem": f"🔍 *Resultados para '{nome}'*\n" + "\n".join(linhas)}

    # ── httpbin (debug puro de HTTP) ────────────────────────────────────────

    async def testar_status(self, code: int) -> dict:
        """GET /status/{code} — dispara o status code pedido, mostra o que
        voltou. Não usa `_get`/`raise_for_status` de propósito: o objetivo
        aqui é justamente observar status != 200 sem virar exceção."""
        try:
            client = get_client("httpbin")
            resp = await client.get(f"/status/{code}")
        except httpx.RequestError as e:
            return {"mensagem": f"❌ Erro de rede ao testar status {code}: {e}"}

        return {"mensagem": f"🔧 httpbin respondeu HTTP {resp.status_code} (pedido: {code})"}

    async def echo_request(self) -> dict:
        """GET /get — mostra de volta os headers/args que o agente mandou."""
        try:
            resp = await self._get("httpbin", "/get", params={"origem": "oraculo-rest-lab"})
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
