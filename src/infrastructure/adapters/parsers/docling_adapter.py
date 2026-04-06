"""
src/infrastructure/adapters/parsers/docling_adapter.py
-------------------------------------------------------
Adapter para o IBM Docling — parser local e gratuito.

Docling v2 extrai texto com compreensão de layout: detecta tabelas,
figuras, cabeçalhos, rodapés e estrutura hierárquica do documento.
É significativamente melhor que pymupdf para PDFs com tabelas complexas
(editais, calendários com layout de tabela).

INSTALAÇÃO:
  pip install docling
  (Baixa modelos de ML automaticamente na primeira execução ~200MB)

VANTAGENS vs pymupdf:
  - Compreensão de tabelas: converte tabelas em Markdown
  - Compreensão hierárquica: detecta H1/H2 e estrutura do texto
  - Suporta DOCX, HTML além de PDF
  - Output em Markdown (ideal para chunking por headers)

DESVANTAGENS:
  - Mais lento (usa modelos de ML)
  - Mais memória (~500MB RAM na primeira chamada)
  - Não ideal para PDFs simples com texto corrido
"""
from __future__ import annotations

import logging
import os

from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)


class DoclingAdapter(IDocumentParser):
    """
    Adapter para IBM Docling.
    Converte PDF/DOCX para texto estruturado com compreensão de layout.
    """

    def __init__(self):
        self._converter = None  # lazy init para não carregar modelos na importação

    def _get_converter(self):
        if self._converter is None:
            try:
                from docling.document_converter import DocumentConverter
                self._converter = DocumentConverter()
                logger.info("✅ Docling converter inicializado")
            except ImportError:
                raise ImportError(
                    "Docling não instalado. Execute: pip install docling"
                )
        return self._converter

    def parse(self, file_path: str, instruction: str = "") -> str:
        """
        Converte documento para texto estruturado via Docling.

        Args:
            file_path: caminho para o arquivo (PDF, DOCX, HTML)
            instruction: ignorado pelo Docling (sem system prompt nativo)

        Returns:
            Texto em Markdown preservando estrutura do documento.
        """
        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""

        logger.info("📄 Docling processando: %s", os.path.basename(file_path))

        try:
            converter = self._get_converter()
            result = converter.convert(file_path)

            # Exporta como Markdown (melhor para chunking hierárquico)
            markdown = result.document.export_to_markdown()

            if not markdown.strip():
                logger.warning("⚠️  Docling: texto vazio para '%s'", file_path)
                return ""

            logger.info(
                "✅ Docling: %d chars extraídos de '%s'",
                len(markdown), os.path.basename(file_path),
            )
            return markdown

        except Exception as e:
            logger.exception("❌ Docling falhou para '%s': %s", file_path, e)
            return ""