"""
src/rag/ingestion/parser_factory.py
-------------------------------------
Factory de parsers de documentos.

PARSERS DISPONÍVEIS (todos gratuitos/locais):
  pymupdf      → extração de texto rápida, para PDFs com texto real
  docling      → IBM Docling, suporta PDF/DOCX/HTML com layout awareness
  marker       → converte PDF→Markdown (ótimo para relatórios e editais)
  unstructured → Unstructured.io local mode, suporta muitos formatos
  txt          → leitura de arquivo texto com detecção de encoding

COMO ADICIONAR UM PARSER:
  1. Criar src/rag/ingestion/parsers/meu_parser.py implementando IDocumentParser
  2. Registrar no dicionário _REGISTRY abaixo
  3. Pronto. Zero mudanças em outros arquivos.

SELEÇÃO AUTOMÁTICA (auto):
  A fábrica inspeciona a extensão do arquivo e o conteúdo (magic bytes)
  para escolher o parser mais adequado automaticamente.
  PDF com pouco texto → marker (OCR via Marker)
  PDF com texto rico → docling (layout) ou pymupdf (velocidade)
  DOCX → docling ou unstructured
  TXT/MD/CSV → txt
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Lazy imports — só carrega a lib quando realmente precisa
# ─────────────────────────────────────────────────────────────────────────────

def _get_pymupdf_parser() -> "IDocumentParser":
    from src.infrastructure.adapters.parsers.pymupdf_adapter import PyMuPDFAdapter
    return PyMuPDFAdapter()


def _get_docling_parser() -> "IDocumentParser":
    """
    Docling (IBM) — parser avançado com compreensão de layout.
    Instalação: pip install docling
    Documentação: https://github.com/DS4SD/docling
    Gratuito, local, sem API key.
    """
    from src.infrastructure.adapters.parsers.docling_adapter import DoclingAdapter
    return DoclingAdapter()


def _get_marker_parser() -> "IDocumentParser":
    """
    Marker — converte PDF para Markdown de alta qualidade.
    Instalação: pip install marker-pdf
    Documentação: https://github.com/VikParuchuri/marker
    Gratuito, local (usa modelos de ML leves).
    Ótimo para PDFs com tabelas complexas e editais.
    """
    from src.infrastructure.adapters.parsers.marker_adapter import MarkerAdapter
    return MarkerAdapter()


def _get_unstructured_parser() -> "IDocumentParser":
    """
    Unstructured.io (modo local, sem API key).
    Instalação: pip install unstructured[pdf,docx]
    Suporta: PDF, DOCX, HTML, PPTX, XLSX, EML, MSG, RTF, ODT, etc.
    Modo local = gratuito. Modo API = pago (não usamos).
    """
    from src.infrastructure.adapters.parsers.unstructured_adapter import UnstructuredAdapter
    return UnstructuredAdapter()


def _get_txt_parser() -> "IDocumentParser":
    from src.infrastructure.adapters.parsers.txt_adapter import TxtAdapter
    return TxtAdapter()


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, callable] = {
    "pymupdf":       _get_pymupdf_parser,
    "docling":       _get_docling_parser,
    "marker":        _get_marker_parser,
    "unstructured":  _get_unstructured_parser,
    "txt":           _get_txt_parser,
}

# Mapeamento extensão → lista de parsers candidatos (ordem de preferência)
_EXT_TO_PARSERS: dict[str, list[str]] = {
    ".pdf":  ["docling", "marker", "pymupdf"],    # docling primeiro (layout-aware)
    ".docx": ["docling", "unstructured"],
    ".doc":  ["unstructured"],
    ".pptx": ["unstructured"],
    ".xlsx": ["unstructured"],
    ".html": ["unstructured"],
    ".htm":  ["unstructured"],
    ".txt":  ["txt"],
    ".md":   ["txt"],
    ".csv":  ["txt"],
}

# Threshold de chars/página para detectar PDF-scan (abaixo = provavelmente imagem)
_MIN_CHARS_PER_PAGE = 50


class ParserFactory:
    """
    Fábrica de parsers de documentos.

    Uso:
        parser = ParserFactory.get("docling")
        texto = parser.parse("/caminho/edital.pdf")

        # Ou automático:
        parser = ParserFactory.auto("/caminho/edital.pdf")
        texto = parser.parse("/caminho/edital.pdf")
    """

    @staticmethod
    def get(parser_name: str) -> "IDocumentParser":
        """Retorna o parser pelo nome. Lança ValueError se não existir."""
        builder = _REGISTRY.get(parser_name.lower())
        if builder is None:
            available = ", ".join(_REGISTRY.keys())
            raise ValueError(f"Parser '{parser_name}' não encontrado. Disponíveis: {available}")
        try:
            return builder()
        except ImportError as e:
            raise ImportError(
                f"Parser '{parser_name}' requer dependências não instaladas: {e}\n"
                f"Instale com: pip install {_INSTALL_HINTS.get(parser_name, parser_name)}"
            ) from e

    @staticmethod
    def auto(file_path: str) -> "IDocumentParser":
        """
        Seleciona automaticamente o melhor parser disponível para o arquivo.

        ALGORITMO:
          1. Detecta extensão
          2. Para PDFs: verifica se tem texto extraível (pymupdf rápido)
             - Se sim → usa docling (layout-aware)
             - Se não (scan) → usa marker (OCR-like via ML)
          3. Para outros formatos → usa unstructured ou txt
          4. Se o parser preferido não está instalado → tenta o próximo
        """
        ext = os.path.splitext(file_path)[1].lower()
        candidates = _EXT_TO_PARSERS.get(ext, ["txt"])

        # Para PDFs, tenta detectar se é scan
        if ext == ".pdf":
            is_scan = _detect_pdf_scan(file_path)
            if is_scan:
                logger.info("📷 PDF scan detectado: %s → usando marker", os.path.basename(file_path))
                candidates = ["marker", "unstructured", "pymupdf"]
            else:
                logger.info("📄 PDF com texto: %s → usando docling", os.path.basename(file_path))
                candidates = ["docling", "pymupdf"]

        for parser_name in candidates:
            try:
                parser = ParserFactory.get(parser_name)
                logger.debug("✅ Parser selecionado: %s para %s", parser_name, os.path.basename(file_path))
                return parser
            except (ImportError, ValueError):
                logger.debug("⏭️  Parser '%s' não disponível, tentando próximo...", parser_name)
                continue

        # Fallback último recurso: txt (sempre disponível)
        logger.warning("⚠️  Nenhum parser ideal disponível para '%s'. Usando txt fallback.", file_path)
        return _get_txt_parser()

    @staticmethod
    def available() -> list[str]:
        """Lista os parsers que estão instalados e funcionando."""
        available = []
        for name, builder in _REGISTRY.items():
            try:
                builder()
                available.append(name)
            except (ImportError, Exception):
                pass
        return available


def _detect_pdf_scan(file_path: str, pages_to_check: int = 3) -> bool:
    """
    Detecta se um PDF é baseado em imagem (scan) ou tem texto real.
    Usa pymupdf para uma verificação rápida sem instalar parser adicional.
    """
    try:
        import fitz
        doc = fitz.open(file_path)
        n_check = min(pages_to_check, doc.page_count)
        if n_check == 0:
            return False
        total_chars = sum(len(doc[i].get_text("text")) for i in range(n_check))
        chars_per_page = total_chars / n_check
        doc.close()
        return chars_per_page < _MIN_CHARS_PER_PAGE
    except Exception:
        return False  # Sem pymupdf: assume que tem texto


_INSTALL_HINTS = {
    "pymupdf":      "pymupdf",
    "docling":      "docling",
    "marker":       "marker-pdf",
    "unstructured": "unstructured[pdf,docx]",
}