"""
src/infrastructure/adapters/parsers/marker_adapter.py
Adaptador para extração avançada com OCR e Machine Learning usando marker-pdf.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import BinaryIO
from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

class MarkerAdapter(IDocumentParser):
    """Adapter para Marker PDF→Markdown converter (Versão 0.3.0+)."""

    def extract_text(self, file_stream: BinaryIO, **kwargs) -> str:
        """
        Extrai o texto. Como o Marker exige um arquivo físico para processar,
        salvamos o stream em um arquivo temporário.
        """
        try:
            # Importações modernas da versão 0.3.0+
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
        except ImportError:
            raise ImportError("Marker não instalado. Execute: pip install marker-pdf")

        # Cria um arquivo temporário com os bytes que vieram do upload
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(file_stream.read())
            tmp_path = tmp_file.name

        try:
            logger.info("📄 Marker processando arquivo temporário: %s", tmp_path)
            
            # Inicializa os modelos de IA do Marker (pode demorar na primeira vez)
            model_dict = create_model_dict()
            
            # Executa a conversão
            converter = PdfConverter(artifact_dict=model_dict)
            rendered = converter(tmp_path)
            
            # Extrai o texto final e os metadados (A versão moderna retorna 3 valores)
            texto, _, _ = text_from_rendered(rendered)
            
            if not texto or not texto.strip():
                logger.warning("⚠️  Marker: texto vazio retornado.")
                return ""
                
            logger.info("✅ Marker extraiu %d caracteres.", len(texto))
            return texto
            
        except Exception as e:
            logger.error("❌ Erro durante a conversão do Marker: %s", e)
            raise ValueError(f"Falha no Marker: {str(e)}") from e
        finally:
            # Limpa o arquivo temporário do servidor
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError as e:
                    logger.warning("Não foi possível remover o arquivo temporário %s: %s", tmp_path, e)

    # Mantemos o método parse antigo caso algum código legado ainda o chame diretamente
    def parse(self, file_path: str, instruction: str = "") -> str:
        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""
            
        with open(file_path, "rb") as f:
            return self.extract_text(f)