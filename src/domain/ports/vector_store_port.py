from abc import ABC, abstractmethod
from typing import List, Dict, Any

class IVectorStorePort(ABC):
    """
    Porta (Interface) para comunicação com o Banco Vetorial.
    Garante que o domínio não saiba se estamos a usar Redis, Postgres, Pinecone, etc.
    """
    
    @abstractmethod
    async def salvar_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """Salva os blocos de texto e os seus embeddings no banco."""
        pass

    @abstractmethod
    async def buscar_contexto(self, query_text: str, k: int, source_filter: str = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retorna os resultados CRUS da busca.
        Deve devolver um dicionário exatamente com este formato:
        {
            "vetorial": [{"id": "...", "content": "...", "source": "..."}, ...],
            "textual": [{"id": "...", "content": "...", "source": "..."}, ...]
        }
        """
        pass