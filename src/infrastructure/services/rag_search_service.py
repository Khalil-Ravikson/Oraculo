"""
src/infrastructure/services/rag_search_service.py
"""
from __future__ import annotations
import logging
import unicodedata
from src.domain.ports.tool_ports import (ICalendarioService, IContatosService, IEditalService, IRAGSearchService, IWikiCTICService, ToolResult)

logger = logging.getLogger(__name__)

_DOC_TYPE_CONFIG: dict[str, dict] = {
    "calendario": {"source_filter": None, "k_vector": 5, "k_text": 8, "max_chars": 1200, "label": "CALENDÁRIO ACADÊMICO"},
    "edital":     {"source_filter": None, "k_vector": 4, "k_text": 10, "max_chars": 1400, "label": "EDITAL PAES 2026"},
    "contatos":   {"source_filter": None, "k_vector": 7, "k_text": 5, "max_chars": 1500, "label": "CONTATOS UEMA"},
    "wiki_ctic":  {"source_filter": None, "k_vector": 5, "k_text": 6, "max_chars": 1500, "label": "WIKI CTIC"},
    "geral":      {"source_filter": None, "k_vector": 6, "k_text": 6, "max_chars": 1500, "label": "DOCUMENTOS GERAIS"},
}

_MSG_NAO_ENCONTRADO = {
    "calendario": "Não encontrei essa data no calendário acadêmico.",
    "edital": "Não encontrei no edital PAES 2026.",
    "contatos": "Não encontrei esse contato.",
    "wiki_ctic": "Não encontrei na Wiki do CTIC.",
    "geral": "Não encontrei essa informação nos documentos disponíveis.",
}

def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("utf-8").lower().strip()

class HybridRAGSearchService(IRAGSearchService):
    def __init__(self, embeddings_model): self._emb = embeddings_model
    async def buscar(self, query: str, doc_type: str, source_filter: str | None = None) -> ToolResult:
        config = _DOC_TYPE_CONFIG.get(doc_type, _DOC_TYPE_CONFIG["geral"])
        sf = source_filter or config["source_filter"]
        query_norm = _normalizar(query)
        try:
            import asyncio
            vetor = await asyncio.to_thread(self._emb.embed_query, query_norm)
            from src.infrastructure.redis_client import busca_hibrida
            resultados = await asyncio.to_thread(busca_hibrida, query_text=query_norm, query_embedding=vetor, source_filter=sf, k_vector=config["k_vector"], k_text=config["k_text"])
            if not resultados:
                return ToolResult.success(message=_MSG_NAO_ENCONTRADO.get(doc_type, _MSG_NAO_ENCONTRADO["geral"]), data={"chunks": [], "found": False})
            blocos = [r["content"].strip() for r in resultados if r.get("content", "").strip()]
            resposta = "\n---\n".join(blocos)[:config["max_chars"]]
            return ToolResult.success(message=resposta, data={"chunks": len(resultados), "found": True, "doc_type": doc_type, "top_score": resultados[0].get("rrf_score", 0) if resultados else 0})
        except Exception as e:
            logger.exception("❌ RAGSearch [%s]: %s", doc_type, e)
            return ToolResult.failure(f"Erro técnico: {e}")

class CalendarioService(ICalendarioService):
    def __init__(self, rag: IRAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult: return await self._rag.buscar(query, doc_type="calendario")
    async def proximos_eventos(self, dias: int = 7) -> ToolResult: return ToolResult.failure("Não implementado nesta versão.")

class EditalService(IEditalService):
    def __init__(self, rag: IRAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult: return await self._rag.buscar(query, doc_type="edital")

class ContatosService(IContatosService):
    def __init__(self, rag: IRAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult: return await self._rag.buscar(query, doc_type="contatos")

class WikiCTICService(IWikiCTICService):
    def __init__(self, rag: IRAGSearchService): self._rag = rag
    async def consultar(self, query: str) -> ToolResult: return await self._rag.buscar(query, doc_type="wiki_ctic")