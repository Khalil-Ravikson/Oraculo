"""
src/infrastructure/observability/search_health.py — índices de busca (Hub v2 Sprint 6a)
=====================================================================================
Alimenta `/hub/infra/search`: lista os índices RediSearch, mostra campos
tipados + parâmetros HNSW, e roda uma busca híbrida de teste (BM25 + vetor)
com os scores separados.

Nada aqui muda índice — é leitura + teste. Recriar/reindexar é Sprint 6b
(`collection_registry`), com aviso forte.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Nomes internos → rótulo humano (não vazar `idx:rag:chunks` na UI)
_ROTULO_INDICE = {
    "idx:rag:chunks": "Documentos (busca semântica)",
    "idx:tools": "Roteamento de ferramentas",
    "checkpoint": "Estado de conversas (motor novo)",
    "checkpoint_write": "Estado de conversas — escrita",
}


def _redis():
    from src.infrastructure.redis_client import get_redis_text
    return get_redis_text()


def rotulo_indice(nome: str) -> str:
    return _ROTULO_INDICE.get(nome, nome)


def _parse_attr(attr: list) -> dict:
    """['identifier','$.content','attribute','content','type','TEXT','WEIGHT','2'] -> dict"""
    d = {}
    it = iter(attr)
    for k in it:
        d[str(k).lower()] = next(it, None)
    return d


def listar_indices() -> list[dict]:
    try:
        r = _redis()
        nomes = r.execute_command("FT._LIST") or []
        out = []
        for raw_nome in nomes:
            nome = raw_nome.decode() if isinstance(raw_nome, bytes) else str(raw_nome)
            try:
                info = r.ft(nome).info()
            except Exception:  # noqa: BLE001
                continue
            campos, vetor = [], None
            for attr in info.get("attributes", []) or []:
                a = _parse_attr(attr)
                tipo = str(a.get("type", "")).upper()
                if tipo == "VECTOR":
                    vetor = {
                        "algoritmo": str(a.get("algorithm", "?")).upper(),
                        "dim": _int(a.get("dim") or a.get("dims")),
                        "metrica": str(a.get("distance_metric", "?")).lower(),
                        "M": _int(a.get("m")),
                        "ef_construction": _int(a.get("ef_construction")),
                        "ef_runtime": _int(a.get("ef_runtime")),
                    }
                    campos.append({"nome": a.get("attribute"), "tipo": "vetor"})
                else:
                    campos.append({
                        "nome": a.get("attribute"),
                        "tipo": {"TEXT": "texto", "TAG": "etiqueta", "NUMERIC": "número"}.get(tipo, tipo.lower()),
                        "peso": _num(a.get("weight")) if tipo == "TEXT" else None,
                    })
            out.append({
                "nome": nome,
                "rotulo": rotulo_indice(nome),
                "num_docs": _int(info.get("num_docs")),
                "num_termos": _int(info.get("num_terms")),
                "tamanho_texto_mb": round(_num(info.get("inverted_sz_mb")), 2),
                "tamanho_vetor_mb": round(_num(info.get("vector_index_sz_mb")), 2),
                "indexando": bool(_int(info.get("indexing"))),
                "percent_indexado": round(_num(info.get("percent_indexed")) * 100, 1),
                "falhas_indexacao": _int(info.get("hash_indexing_failures")),
                "campos": campos,
                "vetor": vetor,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [SEARCH_HEALTH] listar_indices: %s", exc)
        return []


async def testar_busca(query: str, k: int = 6) -> dict:
    """Busca híbrida de teste (BM25 + vetor + RRF) no índice de documentos.
    Usa `redis_client.busca_hibrida` — o mesmo caminho síncrono que as tools
    de produção usam (duas FT.SEARCH + RRF manual), não o `HybridQuery` do
    RedisVL (que emite FT.HYBRID, não suportado nesta versão do Redis Stack).
    Nunca lança."""
    import asyncio

    if not query.strip():
        return {"erro": "Digite uma pergunta para testar."}
    try:
        from src.infrastructure.redis_client import busca_hibrida
        from src.rag.embeddings import get_embeddings

        def _rodar() -> list[dict]:
            emb = get_embeddings().embed_query(query)
            return busca_hibrida(query, emb, k_vector=k, k_text=k)

        resultados = await asyncio.to_thread(_rodar)
        return {
            "total": len(resultados),
            "resultados": [
                {
                    "fonte": rr.get("source", ""),
                    "trecho": (rr.get("content", "") or "")[:400],
                    "score": round(float(rr.get("rrf_score", 0)), 4),
                }
                for rr in resultados[:k]
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [SEARCH_HEALTH] testar_busca: %s", exc)
        return {"erro": str(exc)[:200]}


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
