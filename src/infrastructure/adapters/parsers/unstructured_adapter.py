

"""
src/infrastructure/adapters/parsers/unstructured_adapter.py
Adaptador avançado para extração de texto usando a biblioteca 'unstructured'.
Suporta PDF, DOCX, PPTX, HTML, MD, entre outros, mantendo a semântica.
"""
import logging
from typing import BinaryIO
from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

class UnstructuredAdapter(IDocumentParser):
    """
    Extrai texto estruturado usando partition_auto.
    Requer: pip install "unstructured[all-docs]"
    """
    
    def extract_text(self, file_stream: BinaryIO, **kwargs) -> str:
        try:
            from unstructured.partition.auto import partition
        except ImportError:
            raise ImportError(
                "A biblioteca 'unstructured' não está instalada. "
                "Execute: pip install unstructured"
            )

        try:
            # O Unstructured consegue ler diretamente do fluxo de bytes em memória
            elementos = partition(file=file_stream)
            
            # Junta todos os elementos detectados separados por quebra de linha dupla
            texto_extraido = "\n\n".join([str(el) for el in elementos])
            
            return texto_extraido.strip()
            
        except Exception as e:
            logger.error("❌ Erro ao extrair texto com UnstructuredAdapter: %s", e)
            raise ValueError(f"Falha na extração com Unstructured: {str(e)}") from e