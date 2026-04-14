"""
src/infrastructure/adapters/parsers/txt_adapter.py
Adaptador simples para extrair texto de arquivos .txt, .md, .csv, etc.
"""
import os
import logging
from typing import BinaryIO
from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

class TxtAdapter(IDocumentParser):
    """Adaptador para arquivos de texto puro."""

    def parse(self, file_path: str, instruction: str = "") -> str:
        """
        Método obrigatório da interface IDocumentParser.
        Lê o texto diretamente de um arquivo no disco (usado pelo Celery).
        """
        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""
            
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                texto = f.read()
                logger.info("✅ TxtAdapter leu %d caracteres do arquivo %s", len(texto), file_path)
                return texto
        except Exception as e:
            logger.error("❌ Erro ao ler arquivo de texto %s: %s", file_path, e)
            return ""

    def extract_text(self, file_stream: BinaryIO, **kwargs) -> str:
        """
        Lê o arquivo binário direto da memória.
        Usado pela API do ChunkViz durante o upload em tempo real.
        """
        content = file_stream.read()
        
        if isinstance(content, str):
            return content
            
        return content.decode('utf-8', errors='ignore')