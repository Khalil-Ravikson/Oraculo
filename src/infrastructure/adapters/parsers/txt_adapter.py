"""
src/infrastructure/adapters/parsers/txt_adapter.py
Adaptador simples para extrair texto de arquivos .txt, .md, .csv, etc.
"""
from typing import BinaryIO
from src.domain.ports.document_parser import IDocumentParser

class TxtAdapter(IDocumentParser):
    
    def extract_text(self, file_stream: BinaryIO, **kwargs) -> str:
        """
        Lê o arquivo binário e decodifica para string.
        Usa errors='ignore' para evitar quebra com caracteres estranhos.
        """
        content = file_stream.read()
        
        if isinstance(content, str):
            return content
            
        return content.decode('utf-8', errors='ignore')