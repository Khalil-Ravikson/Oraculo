from .registry import register
from src.infrastructure.services.rag_search_service import RAGSearchService
from src.infrastructure.services.synthesis_service import SynthesisService

_rag = RAGSearchService()
_syn = SynthesisService()

@register("vector_search")
async def vector_search(p: dict) -> dict:
    result = await _rag.buscar(
        p["query"], doc_type=p.get("doc_type", "geral"),
        rota=p.get("rota", "GERAL"),
        fatos=p.get("fatos", []),
    )
    return {"chunks": result.data.get("chunks", []), "context": result.message}

@register("synthesis")
async def synthesis(p: dict) -> dict:
    result = await _syn.sintetizar(
        query=p["query"],
        chunks=p.get("chunks", []),
        historico=p.get("historico", ""),
        fatos=p.get("fatos", []),
    )
    return {"answer": result.answer, "tokens": result.tokens_in + result.tokens_out}