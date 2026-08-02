"""
mcp_lab/tools.py
====================
Uma função async por tool MCP consumida (mesmo contrato de
`rest_lab/tools.py`: sempre devolve `{"mensagem": str}` já formatado pra
WhatsApp, nunca deixa exceção escapar). Diferença de mecânica: aqui não há
`httpx.get`/`.raise_for_status()` — é `session.call_tool(nome, args)` do SDK
`mcp`, que devolve um `CallToolResult` com `.content` (lista de blocos,
tipicamente `TextContent`) e `.is_error`.

Tools do servidor StackExchange consumidas nesta rodada: `search_questions`
e `get_answers`. As outras 3 (`stack_get_user`, `list_questions_by_tag`,
`stack_tags`) seguem exatamente o mesmo padrão se forem adicionadas depois
— não implementadas agora (fora de escopo do plano desta rodada).
"""
from __future__ import annotations

import json
import re

from mcp_lab.clients import brave_session, github_session, stackexchange_session

_LIMITE_LISTA = 3  # cap de itens numa lista formatada — pensando em WhatsApp
_RE_TAG_HTML = re.compile(r"<[^>]+>")


def _sem_html(texto: str) -> str:
    """A API StackExchange devolve corpo de resposta em HTML cru — sem
    tratamento isso vaza `<p>`/`<a href=...>` direto na mensagem do WhatsApp."""
    return _RE_TAG_HTML.sub("", texto).strip()


def _extrair_dados(resultado) -> dict | list:
    """`CallToolResult.content` é uma lista de blocos (normalmente
    `TextContent`); o servidor StackExchange devolve JSON serializado como
    texto. Concatena os blocos de texto e faz o parse."""
    texto = "".join(bloco.text for bloco in resultado.content if hasattr(bloco, "text"))
    return json.loads(texto)


async def buscar_perguntas(query: str, site: str = "stackoverflow") -> dict:
    """Tool `search_questions` — top perguntas por relevância."""
    try:
        async with stackexchange_session() as session:
            resultado = await session.call_tool(
                "search_questions", {"query": query, "site": site}
            )
    except Exception as e:  # erro de rede/protocolo MCP — nunca sobe pro router
        return {"mensagem": f"❌ Erro ao buscar perguntas: {e}"}

    if resultado.is_error:
        return {"mensagem": f"❌ Servidor MCP retornou erro: {_extrair_dados(resultado)}"}

    perguntas = _extrair_dados(resultado)
    if isinstance(perguntas, dict):
        perguntas = perguntas.get("items", perguntas.get("questions", []))
    if not perguntas:
        return {"mensagem": f"🔍 Nenhuma pergunta encontrada para '{query}'."}

    linhas = []
    for p in perguntas[:_LIMITE_LISTA]:
        linhas.append(
            f"#{p.get('question_id', '?')} — {p.get('title', '(sem título)')}\n"
            f"   ⭐ {p.get('score', 0)} | 💬 {p.get('answer_count', 0)} respostas | {p.get('link', '')}"
        )
    return {"mensagem": f"🔍 *StackExchange — '{query}'*\n" + "\n".join(linhas)}


async def obter_respostas(question_id: int, site: str = "stackoverflow") -> dict:
    """Tool `get_answers` — respostas de uma pergunta pelo ID."""
    try:
        async with stackexchange_session() as session:
            resultado = await session.call_tool(
                "get_answers", {"question_id": question_id, "site": site}
            )
    except Exception as e:
        return {"mensagem": f"❌ Erro ao buscar respostas: {e}"}

    if resultado.is_error:
        return {"mensagem": f"❌ Servidor MCP retornou erro: {_extrair_dados(resultado)}"}

    respostas = _extrair_dados(resultado)
    if isinstance(respostas, dict):
        respostas = respostas.get("items", respostas.get("answers", []))
    if not respostas:
        return {"mensagem": f"❌ Sem respostas para a pergunta {question_id}."}

    linhas = []
    for a in respostas[:_LIMITE_LISTA]:
        marca = "✅" if a.get("is_accepted") else "▫️"
        corpo = _sem_html(a.get("body") or "")[:200]
        linhas.append(f"{marca} ⭐ {a.get('score', 0)}\n{corpo}...")
    return {"mensagem": f"💬 *Respostas — pergunta {question_id}*\n\n" + "\n\n".join(linhas)}


# ── Brave Search (web) ───────────────────────────────────────────────────────
# Via MCP de verdade (gateway pipeworx, BYO key). Só `web_search` — o proxy
# do pipeworx não expõe image_search/video_search do Brave (checado na doc,
# `pipeworx.io/docs/reference/brave-search`, só lista web_search/news_search).

async def buscar_web(query: str) -> dict:
    """Tool `web_search` do Brave — formato de resposta não confirmado ao
    vivo nesta sessão (sem acesso a Docker pra testar), parsing defensivo
    tentando os formatos mais prováveis. Se quebrar, `run_test.py` ajuda a
    ver o payload cru rápido."""
    from src.infrastructure.settings import settings
    if not settings.BRAVE_API_KEY:
        return {"mensagem": "❌ BRAVE_API_KEY não configurada no .env."}

    try:
        async with brave_session() as session:
            resultado = await session.call_tool(
                "web_search",
                {"query": query, "count": _LIMITE_LISTA, "_apiKey": settings.BRAVE_API_KEY},
            )
    except Exception as e:
        return {"mensagem": f"❌ Erro na busca web: {e}"}

    if resultado.is_error:
        return {"mensagem": f"❌ Brave retornou erro: {_extrair_dados(resultado)}"}

    dados = _extrair_dados(resultado)
    if isinstance(dados, list):
        itens = dados
    elif isinstance(dados, dict):
        itens = dados.get("results") or dados.get("web", {}).get("results", [])
    else:
        itens = []
    if not itens:
        return {"mensagem": f"🔍 Nenhum resultado encontrado para '{query}'."}

    linhas = []
    for r in itens[:_LIMITE_LISTA]:
        titulo = r.get("title", "(sem título)")
        url = r.get("url", r.get("link", ""))
        desc = _sem_html(r.get("description", r.get("snippet", "")))[:120]
        linhas.append(f"🔗 *{titulo}*\n{desc}\n{url}")
    return {"mensagem": f"🌐 *Busca web — '{query}'*\n\n" + "\n\n".join(linhas)}


# ── Brave Search (imagem) — REST direto, NÃO MCP ────────────────────────────
# O proxy MCP do pipeworx não cobre image_search do Brave (ver comentário
# acima) — chamando a API REST do Brave direto, mesmo padrão do rest_lab/
# (httpx puro), só que vivendo aqui porque é a mesma chave/mesmo laboratório.

async def buscar_imagem(query: str, chat_id: str) -> dict:
    """Busca 1 imagem e MANDA direto pro WhatsApp via URL (Evolution aceita
    mídia por URL pública — a Brave já devolve isso, sem precisar baixar+
    base64 como fizemos pro YouTube em `worker_media_download.py`)."""
    from src.infrastructure.settings import settings
    import httpx

    if not settings.BRAVE_API_KEY:
        return {"mensagem": "❌ BRAVE_API_KEY não configurada no .env."}
    if not chat_id:
        return {"mensagem": "❌ Sem destino pra enviar a imagem (chat_id ausente)."}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/images/search",
                params={"q": query, "count": 1},
                headers={"Accept": "application/json", "X-Subscription-Token": settings.BRAVE_API_KEY},
            )
            resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return {"mensagem": f"❌ Erro na busca de imagem: {e}"}

    resultados = resp.json().get("results", [])
    if not resultados:
        return {"mensagem": f"🔍 Nenhuma imagem encontrada para '{query}'."}

    img = resultados[0]
    url_imagem = (img.get("properties") or {}).get("url") or (img.get("thumbnail") or {}).get("src")
    if not url_imagem:
        return {"mensagem": f"❌ Imagem encontrada mas sem URL utilizável para '{query}'."}

    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
    gateway = EvolutionAdapter()
    ok = await gateway.enviar_midia_url(
        chat_id, url_imagem, mediatype="image", mimetype="image/jpeg",
        caption=img.get("title", query),
    )
    if ok:
        return {"mensagem": f"🖼️ Imagem sobre '{query}' enviada."}
    return {"mensagem": f"❌ Achei a imagem mas não consegui enviar:\n{url_imagem}"}


# ── GitHub ───────────────────────────────────────────────────────────────────
# Via MCP de verdade (gateway pipeworx, BYO key — token com só permissões de
# conta/leitura, ver .claude.md; busca pública não precisa de escopo de repo).

async def buscar_repos_github(query: str) -> dict:
    """Tool `search_repos`."""
    from src.infrastructure.settings import settings
    if not settings.GITHUB_API_KEY:
        return {"mensagem": "❌ GITHUB_API_KEY não configurada no .env."}

    try:
        async with github_session() as session:
            resultado = await session.call_tool(
                "search_repos", {"query": query, "_apiKey": settings.GITHUB_API_KEY}
            )
    except Exception as e:
        return {"mensagem": f"❌ Erro na busca de repositórios: {e}"}

    if resultado.is_error:
        return {"mensagem": f"❌ GitHub MCP retornou erro: {_extrair_dados(resultado)}"}

    dados = _extrair_dados(resultado)
    if isinstance(dados, list):
        repos = dados
    elif isinstance(dados, dict):
        repos = dados.get("repos") or dados.get("items") or dados.get("repositories", [])
    else:
        repos = []
    if not repos:
        return {"mensagem": f"🔍 Nenhum repositório encontrado para '{query}'."}

    linhas = []
    for r in repos[:_LIMITE_LISTA]:
        nome = r.get("full_name", r.get("name", "?"))
        estrelas = r.get("stargazers_count", r.get("stars", 0))
        desc = (r.get("description") or "")[:100]
        url = r.get("html_url", r.get("url", ""))
        linhas.append(f"📦 *{nome}* ⭐ {estrelas}\n{desc}\n{url}")
    return {"mensagem": f"🐙 *GitHub — '{query}'*\n\n" + "\n\n".join(linhas)}


async def perfil_github(usuario: str) -> dict:
    """Tool `get_user`."""
    from src.infrastructure.settings import settings
    if not settings.GITHUB_API_KEY:
        return {"mensagem": "❌ GITHUB_API_KEY não configurada no .env."}

    try:
        async with github_session() as session:
            resultado = await session.call_tool(
                "get_user", {"username": usuario, "_apiKey": settings.GITHUB_API_KEY}
            )
    except Exception as e:
        return {"mensagem": f"❌ Erro ao buscar perfil: {e}"}

    if resultado.is_error:
        return {"mensagem": f"❌ GitHub MCP retornou erro: {_extrair_dados(resultado)}"}

    p = _extrair_dados(resultado)
    if isinstance(p, list):
        p = p[0] if p else {}
    if not p:
        return {"mensagem": f"❌ Usuário '{usuario}' não encontrado."}

    return {
        "mensagem": (
            f"🐙 *{p.get('name') or p.get('login', usuario)}* (@{p.get('login', usuario)})\n"
            f"📝 {p.get('bio') or '(sem bio)'}\n"
            f"📦 {p.get('public_repos', '?')} repos públicos | 👥 {p.get('followers', '?')} seguidores\n"
            f"🔗 {p.get('html_url', p.get('url', ''))}"
        )
    }
