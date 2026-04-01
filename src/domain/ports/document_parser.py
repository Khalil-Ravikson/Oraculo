# src/domain/ports/document_parser.py
from abc import ABC, abstractmethod

class IDocumentParser(ABC):
    """
    Interface (Porta) para extratores de texto.
    O nosso Caso de Uso de Ingestão só conhecerá esta interface,
    nunca as bibliotecas reais (PyMuPDF, LlamaParse, etc).
    """

    @abstractmethod
    def parse(self, file_path: str, instruction: str = "") -> str:
        """
        Lê um arquivo físico e retorna todo o seu conteúdo como uma única string.
        
        Args:
            file_path: O caminho do arquivo no disco.
            instruction: (Opcional) Instrução de sistema para parsers baseados em IA.
            
        Returns:
            O texto limpo extraído do documento.
        """
        pass