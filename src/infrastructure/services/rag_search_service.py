"""
src/infrastructure/services/rag_search_service.py
---------------------------------------------------
Implementação do IRAGSearchService + serviços especializados por doc_type.

ELIMINA a duplicação de lógica de busca entre calendar_tool, tool_edital e tool_contatos.
Todos usam a mesma lógica de busca híbrida — o que muda é o source_filter e k_text/k_vector.

CONFIGURAÇÃO POR DOC_TYPE:
  Cada tipo de documento tem parâmetros otimizados de busca.
  Adicionar novo tipo = adicionar entrada em _DOC_TYPE_CONFIG.
"""
from __future__ import annotations

import logging
import unicodedata

from src.domain.ports.tool_ports import (
    ICalendarioService,
    IContatosService,
    IEditalService,
    IRAGSearchService,
    IWikiCTICService,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Configuração de busca por tipo de documento
_DOC_TYPE_CONFIG: dict[str, dict] = {
    "calendario": {
        "source_filter": "calendario-academico-2026.pdf",
        "k_vector": 5,
        "k_text": 8,       # BM25 é crítico para datas exatas
        "max_chars": 1200,
        "label": "CALENDÁRIO ACADÊMICO UEMA 2026",
    },
    "edital": {
        "source_filter": "edital_paes_2026.pdf",
        "k_vector": 4,
        "k_text": 10,      # edital tem muitas siglas exatas (AC, BR-PPI)
        "max_chars": 1400,
        "label": "EDITAL PAES 2026",
    },
    "contatos": {
        "source_filter": "guia_contatos_2025.pdf",
        "k_vector": 7,     # variações semânticas de nomes de setores
        "k_text": 5,
        "max_chars": 1500,
        "label": "CONTATOS UEMA",
    },
    "wiki_ctic": {
        "source_filter": None,     # busca em todas as páginas da wiki
        "k_vector": 5,
        "k_text": 6,
        "max_chars": 1500,
        "label": "WIKI CTIC/UEMA",
    },
    "geral": {
        "source_filter": None,
        "k_vector": 6,
        "k_text": 6,
        "max_chars": 1500,
        "label": "DOCUMENTOS UEMA",
    },
}

_MSG_NAO_ENCONTRADO = {
    "calendario": "Não encontrei essa data no calendário acadêmico. Tente: matrícula, feriado, início das aulas, trancamento.",
    "edital": "Não encontrei no edital PAES 2026. Tente: vagas, cotas, AC, PcD, BR-PPI, inscrição, documentos.",
    "contatos": "Não encontrei esse contato. Tente o nome do setor, curso ou sigla: PROG, CTIC, CECEN.",
    "wiki_ctic": "Não encontrei na Wiki do CTIC. Tente: senha, wifi, SIGAA, e-mail institucional, suporte.",
    "geral": "Não encontrei essa informação nos documentos disponíveis.",
}


def _normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("utf-8")
    return s.lower().strip()


class HybridRAGSearchService(IRAGSearchService):
    """
    Serviço de busca RAG híbrida (BM25 + Vector + RRF).
    Único ponto de entrada para todas as buscas nos documentos.
    """

    def __init__(self, embeddings_model):
        self._emb = embeddings_model

    async def buscar(self, query: str, doc_type: str, source_filter: str | None = None) -> ToolResult:
        """Busca híbrida configurada para o doc_type."""
        config = _DOC_TYPE_CONFIG.get(doc_type, _DOC_TYPE_CONFIG["geral"])
        sf = source_filter or config["source_filter"]
        query_norm = _normalizar(query)

        try:
            import asyncio
            vetor = await asyncio.to_thread(self._emb.embed_query, query_norm)

            from src.infrastructure.redis_client import busca_hibrida
            resultados = await asyncio.to_thread(
                busca_hibrida,
                query_text=query_norm,
                query_embedding=vetor,
                source_filter=sf,
                k_vector=config["k_vector"],
                k_text=config["k_text"],
            )

            if not resultados:
                return ToolResult.success(
                    message=_MSG_NAO_ENCONTRADO.get(doc_type, _MSG_NAO_ENCONTRADO["geral"]),
                    data={"chunks": [], "found": False},
                )

            blocos = [r["content"].strip() for r in resultados if r.get("content", "").strip()]
            resposta = "\n---\n".join(blocos)
            if len(resposta) > config["max_chars"]:
                resposta = resposta[:config["max_chars"]] + "\n[...truncado]"

            logger.debug("✅ RAG [%s]: %d chunks | query='%.40s'", doc_type, len(resultados), query)

            return ToolResult.success(
                message=resposta,
                data={
                    "chunks": len(resultados),
                    "found": True,
                    "doc_type": doc_type,
                    "top_score": resultados[0].get("rrf_score", 0) if resultados else 0,
                },
            )
        except Exception as e:
            logger.exception("❌ RAGSearch [%s]: %s", doc_type, e)
            return ToolResult.failure(f"Erro técnico na busca: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Serviços especializados (delegam para HybridRAGSearchService)
# ─────────────────────────────────────────────────────────────────────────────

class CalendarioService(ICalendarioService):
    def __init__(self, rag: IRAGSearchService):
        self._rag = rag

    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="calendario")

    async def proximos_eventos(self, dias: int = 7) -> ToolResult:
        try:
            from src.rag.calendar_parser import buscar_eventos_proximos
            import asyncio
            eventos = await asyncio.to_thread(buscar_eventos_proximos, dias_frente=dias)
            if not eventos:
                return ToolResult.success(message="Nenhum evento nos próximos dias.", data={"eventos": []})

            lista = [{"nome": e.nome, "data": str(e.data_inicio), "dias": e.dias_restantes, "emoji": e.emoji}
                     for e in eventos[:10]]
            return ToolResult.success(
                message=f"Encontrei {len(lista)} evento(s) nos próximos {dias} dias.",
                data={"eventos": lista},
            )
        except Exception as e:
            return ToolResult.failure(str(e))


class EditalService(IEditalService):
    def __init__(self, rag: IRAGSearchService):
        self._rag = rag

    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="edital")


class ContatosService(IContatosService):
    def __init__(self, rag: IRAGSearchService):
        self._rag = rag

    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="contatos")


class WikiCTICService(IWikiCTICService):
    def __init__(self, rag: IRAGSearchService):
        self._rag = rag

    async def consultar(self, query: str) -> ToolResult:
        return await self._rag.buscar(query, doc_type="wiki_ctic")