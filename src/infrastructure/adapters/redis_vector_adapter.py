import asyncio
import logging
from typing import List, Dict, Any

from src.domain.ports.vector_store_port import IVectorStorePort

# Importamos suas funções reais de infraestrutura
from src.infrastructure.redis_client import salvar_chunk, busca_hibrida
from src.rag.embeddings import get_embeddings

logger = logging.getLogger(__name__)

class RedisVectorAdapter(IVectorStorePort):
    """
    Adaptador Real que conecta a Clean Architecture ao Redis Stack.
    """
    def __init__(self):
        # Puxa o Singleton (Google agora, Local no futuro)
        self.embeddings_model = get_embeddings()

    async def salvar_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """Gera os embeddings e salva no Redis sem travar a thread principal."""
        if not chunks:
            return

        logger.info(f"🧠 Gerando embeddings para {len(chunks)} chunks...")
        textos = [c["content"] for c in chunks]
        
        # Gera os vetores em background
        embeddings = await asyncio.to_thread(self.embeddings_model.embed_documents, textos)

        logger.info("💾 Salvando no Redis Stack...")
        for chunk, embedding in zip(chunks, embeddings):
            await asyncio.to_thread(
                salvar_chunk,
                chunk_id=chunk["chunk_id"],
                content=chunk["content"],
                source=chunk["source"],
                doc_type=chunk["doc_type"],
                embedding=embedding,
                chunk_index=chunk.get("metadata", {}).get("chunk_index", 0),
                metadata=chunk.get("metadata", {})
            )
        logger.info("✅ Todos os chunks foram salvos no Redis!")

    async def buscar_hibrido(self, query_text: str, k_vector: int, k_text: int, source_filter: str = None) -> List[Dict[str, Any]]:
        """Gera o vetor da pergunta e faz a busca (BM25 + Semântica)."""
        
        # Gera o vetor apenas para a pergunta do aluno
        vetor_query = await asyncio.to_thread(self.embeddings_model.embed_query, query_text)

        # Dispara a busca real no Redis Stack
        resultados_raw = await asyncio.to_thread(
            busca_hibrida,
            query_text=query_text,
            query_embedding=vetor_query,
            source_filter=source_filter,
            k_vector=k_vector,
            k_text=k_text
        )
        return resultados_raw