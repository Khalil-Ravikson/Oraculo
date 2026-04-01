import asyncio
import logging
import struct
import re
from typing import List, Dict, Any
from redis.commands.search.query import Query
from src.domain.ports.vector_store_port import IVectorStorePort
from src.infrastructure.database.redis_connection import get_async_redis
from src.rag.embeddings import get_embeddings

logger = logging.getLogger(__name__)

class RedisVectorAdapter(IVectorStorePort):
    def __init__(self):
        self.embeddings_model = get_embeddings()
        self.idx_chunks = "idx:rag:chunks"
        self.prefix_chunks = "rag:chunk:"

    async def salvar_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        if not chunks: return
        
        textos = [c["content"] for c in chunks]
        embeddings = await asyncio.to_thread(self.embeddings_model.embed_documents, textos)

        r = await get_async_redis()
        async with r.pipeline(transaction=True) as pipe:
            for chunk, emb in zip(chunks, embeddings):
                key = f"{self.prefix_chunks}{chunk['source']}:{chunk['chunk_id']}"
                doc = {
                    "content": chunk["content"],
                    "source": chunk["source"],
                    "doc_type": chunk["doc_type"],
                    "chunk_index": chunk.get("metadata", {}).get("chunk_index", 0),
                    "embedding": emb,
                    "metadata": chunk.get("metadata", {})
                }
                pipe.json().set(key, "$", doc)
            await pipe.execute()
        logger.info(f"✅ {len(chunks)} chunks indexados no Redis.")

    async def buscar_contexto(self, query_text: str, k: int, source_filter: str = None) -> Dict[str, List[Dict[str, Any]]]:
        r = await get_async_redis()
        vetor_query = await asyncio.to_thread(self.embeddings_model.embed_query, query_text)
        emb_bytes = struct.pack(f"{len(vetor_query)}f", *vetor_query)

        # Query Vetorial (KNN)
        v_str = f"*=>[KNN {k} @embedding $vec AS score]"
        if source_filter:
            v_str = f"(@source:{{{source_filter.replace('.', '\\.')}}})=>[KNN {k} @embedding $vec AS score]"
        
        q_vec = Query(v_str).sort_by("score").dialect(2).paging(0, k).return_fields("content", "source", "doc_type")

        # Query Textual (BM25) - Apenas limpeza básica de syntax
        safe_text = re.sub(r'[!@\[\]{}()|~^]', ' ', query_text).strip()
        q_txt = Query(safe_text).paging(0, k).return_fields("content", "source", "doc_type")

        # Execução paralela (Gather)
        v_task = r.ft(self.idx_chunks).search(q_vec, {"vec": emb_bytes})
        t_task = r.ft(self.idx_chunks).search(q_txt)
        
        v_res, t_res = await asyncio.gather(v_task, t_task)

        return {
            "vetorial": [{"id": d.id, "content": d.content, "source": d.source} for d in v_res.docs],
            "textual": [{"id": d.id, "content": d.content, "source": d.source} for d in t_res.docs]
        }