from abc import ABC, abstractmethod
from typing import List, Dict, Any

class IVectorStorePort(ABC):
    """Porta para o Banco de Dados Vetorial (Ex: Redis Stack, PGVector)."""

    @abstractmethod
    async def salvar_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Salva uma lista de chunks no banco vetorial.
        O formato esperado do dict é: 
        {'chunk_id': str, 'content': str, 'source': str, 'doc_type': str, 'metadata': dict}
        """
        pass

    @abstractmethod
    async def buscar_hibrido(self, query: str, k_vector: int, k_text: int, source_filter: str = None) -> List[Dict[str, Any]]:
        """Busca combinando Vetores (Semântica) e Palavras-Chave (BM25)."""
        pass