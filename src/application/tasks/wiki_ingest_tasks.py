"""
src/application/tasks/wiki_ingest_tasks.py
==========================================
Ingestão em lote da wiki da CTIC — a base de conhecimento do v1.

## Por que esta task existe

Todas as peças já estavam construídas e nenhuma tinha gatilho:

* `scraping/.../dokuwiki/discovery.py` enumera **todas** as páginas da wiki
  numa requisição só, pelo índice nativo do DokuWiki (`?do=index`);
* `.../dokuwiki/hierarchy.py` reconstrói `sistema`/`modulo` pelo grafo de
  links entre páginas — exatamente os campos que as opções "SIGAA" e "SIPAC"
  do menu usam para filtrar;
* `ScrapingService._ingest_to_rag` grava os chunks com `doc_type`, `sistema`
  e `modulo` corretos.

Faltava alguém chamar isso em sequência. Enquanto faltou, o índice ficou com
37 chunks de teste, todos `doc_type=geral`, e **nenhuma pergunta do menu
encontrava resposta** (ver `docs/ESTADO_ATUAL.md` §4.5).

## Decisões de desenho

**Concorrência limitada, não `gather` em tudo.** A wiki é um servidor da
própria universidade e a lista costuma ter centenas de páginas; disparar tudo
de uma vez é um ataque de negação de serviço contra o próprio cliente.
`settings.WIKI_INGEST_CONCORRENCIA` (default 4) segura isso, e o
`AntiBlockManager` já espaça cada requisição.

**Progresso no Redis, não no resultado da task.** Uma ingestão de centenas de
páginas leva minutos; quem disparou precisa ver andamento, não um resultado
final que só chega no fim. A chave `wiki:ingest:status` é lida pelo painel.

**Nunca derruba tudo por causa de uma página.** Página que falha entra na
contagem de erros e o lote segue. Uma wiki real tem página quebrada, e
abortar no meio deixaria a base pela metade sem ninguém saber quais faltaram.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from src.infrastructure.celery_app import celery_app, run_in_worker_loop

logger = logging.getLogger(__name__)

STATUS_KEY = "wiki:ingest:status"
_STATUS_TTL = 86400


def _publicar_status(dados: dict[str, Any]) -> None:
    """Melhor-esforço: perder o progresso não pode derrubar a ingestão."""
    try:
        from src.infrastructure.redis_client import get_redis_text

        get_redis_text().setex(STATUS_KEY, _STATUS_TTL, json.dumps(dados, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.debug("Falha ao publicar status da ingestão da wiki (ignorado).", exc_info=True)


def ler_status() -> dict[str, Any] | None:
    try:
        from src.infrastructure.redis_client import get_redis_text

        raw = get_redis_text().get(STATUS_KEY)
        if not raw:
            return None
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:  # noqa: BLE001
        return None


async def descobrir(url_base: str = "") -> list[str]:
    """Lista os `page_id` da wiki, sem ingerir nada.

    Separado da ingestão de propósito: ver o que vai entrar antes de gravar é
    a diferença entre uma ação revisável e uma surpresa."""
    from src.infrastructure.scraping.implementations.dokuwiki.discovery import descobrir_paginas
    from src.infrastructure.settings import settings

    return await descobrir_paginas(url_base or settings.WIKI_CTIC_URL)


async def _ingerir(page_ids: list[str], url_base: str, forcar: bool) -> dict[str, Any]:
    from src.infrastructure.redis_client import get_redis_text
    from src.infrastructure.scraping.implementations.dokuwiki.discovery import montar_requests
    from src.infrastructure.scraping.scraping_service import build_default_scraping_service
    from src.infrastructure.settings import settings

    try:
        redis_client = get_redis_text()
    except Exception:  # noqa: BLE001 — sem cache a ingestão ainda roda
        redis_client = None

    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.services import taxonomia_store

    wiki_hubs: dict[str, tuple[str, str]] = {}
    try:
        async with AsyncSessionLocal() as session:
            wiki_hubs = await taxonomia_store.listar_hubs_dict(session)
    except Exception as exc:  # noqa: BLE001 — sem taxonomia, tudo cai em "Geral"
        logger.warning("⚠️  [WIKI] Falha ao carregar taxonomia: %s", exc)

    servico = build_default_scraping_service(
        redis_client=redis_client, ingest_to_rag=True, wiki_hubs=wiki_hubs,
    )
    pedidos = montar_requests(url_base, page_ids)
    for pedido in pedidos:
        pedido.force_refresh = forcar

    total = len(pedidos)
    ok = 0
    falhas: list[str] = []
    inicio = time.time()

    _publicar_status({
        "estado": "rodando", "total": total, "concluidos": 0,
        "ok": 0, "falhas": 0, "iniciado_em": inicio,
    })

    limite = asyncio.Semaphore(max(settings.WIKI_INGEST_CONCORRENCIA, 1))

    async def _uma(pedido):
        async with limite:
            try:
                return await servico.scrape(pedido)
            except Exception as exc:  # noqa: BLE001
                logger.warning("⚠️  [WIKI] %s falhou: %s", pedido.url, exc)
                return None

    # Fatia em blocos só para publicar progresso — dentro de cada bloco a
    # concorrência real é a do semáforo.
    passo = max(settings.WIKI_INGEST_CONCORRENCIA, 1) * 4
    for i in range(0, total, passo):
        bloco = pedidos[i:i + passo]
        resultados = await asyncio.gather(*(_uma(p) for p in bloco))

        for pedido, resultado in zip(bloco, resultados):
            if resultado is not None and getattr(resultado, "ok", False):
                ok += 1
            else:
                falhas.append(pedido.metadata.get("page_id") or pedido.url)

        _publicar_status({
            "estado": "rodando", "total": total, "concluidos": min(i + passo, total),
            "ok": ok, "falhas": len(falhas), "iniciado_em": inicio,
        })

    resumo = {
        "estado": "concluido", "total": total, "concluidos": total,
        "ok": ok, "falhas": len(falhas),
        "paginas_com_falha": falhas[:50],
        "iniciado_em": inicio, "duracao_s": round(time.time() - inicio, 1),
    }
    _publicar_status(resumo)
    logger.info("📚 [WIKI] ingestão concluída: %d/%d páginas, %d falhas.", ok, total, len(falhas))
    return resumo


@celery_app.task(name="ingerir_wiki_ctic", bind=True)
def ingerir_wiki_ctic_task(
    self,
    page_ids: list[str] | None = None,
    url_base: str = "",
    forcar: bool = False,
) -> dict[str, Any]:
    """Descobre (se preciso) e ingere a wiki inteira.

    `page_ids` vazio = descobre tudo. `forcar=True` ignora o cache de
    scraping e rebaixa toda página, mesmo inalterada — use só quando o
    formato do chunk ou a taxonomia mudarem, porque é o caminho caro.
    """
    from src.infrastructure.settings import settings

    base = url_base or settings.WIKI_CTIC_URL

    async def _run():
        ids = page_ids or await descobrir(base)
        if not ids:
            _publicar_status({"estado": "concluido", "total": 0, "concluidos": 0,
                              "ok": 0, "falhas": 0, "erro": "Nenhuma página encontrada."})
            return {"total": 0, "ok": 0, "falhas": 0}
        return await _ingerir(ids, base, forcar)

    try:
        return run_in_worker_loop(_run())
    except Exception as exc:  # noqa: BLE001
        logger.exception("❌ [WIKI] ingestão em lote falhou")
        _publicar_status({"estado": "erro", "erro": str(exc)[:300]})
        raise


@celery_app.task(name="reingerir_wiki_ctic_periodico")
def reingerir_wiki_ctic_periodico_task() -> dict[str, Any]:
    """Revisita a wiki periodicamente (agendador).

    **Sem `forcar`**: o cache de scraping devolve página inalterada sem
    rebaixar nem re-embeddar, então uma wiki parada custa quase nada. O que
    muda entra; o resto é barato."""
    return ingerir_wiki_ctic_task(page_ids=None, url_base="", forcar=False)
