"""
src/infrastructure/adapters/parsers/marker_adapter.py
------------------------------------------------------
Adapter para o Marker — conversor PDF→Markdown.

Marker usa modelos de ML leves para converter PDFs com alta fidelidade
para Markdown, incluindo tabelas, equações e formatação.

INSTALAÇÃO:
  pip install marker-pdf

QUANDO USAR:
  - PDFs com layout complexo (colunas, tabelas misturadas com texto)
  - PDFs de editais e regulamentos com formatação específica
  - Quando docling é lento demais ou consome muita memória

GRATUITO: Marker é open-source, roda inteiramente local.
Documentação: https://github.com/VikParuchuri/marker
"""
from __future__ import annotations

import logging
import os

from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)


class MarkerAdapter(IDocumentParser):
    """Adapter para Marker PDF→Markdown converter."""

    def parse(self, file_path: str, instruction: str = "") -> str:
        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""

        logger.info("📄 Marker processando: %s", os.path.basename(file_path))

        try:
            # Marker v1.x API
            from marker.convert import convert_single_pdf
            from marker.models import load_all_models

            models = load_all_models()
            full_text, _, _ = convert_single_pdf(file_path, models)

            if not full_text or not full_text.strip():
                logger.warning("⚠️  Marker: texto vazio para '%s'", file_path)
                return ""

            logger.info(
                "✅ Marker: %d chars extraídos de '%s'",
                len(full_text), os.path.basename(file_path),
            )
            return full_text

        except ImportError:
            raise ImportError("Marker não instalado. Execute: pip install marker-pdf")
        except Exception as e:
            # Tenta API v2 (marker-pdf >= 1.0)
            try:
                return self._parse_v2(file_path)
            except Exception:
                logger.exception("❌ Marker falhou para '%s': %s", file_path, e)
                return ""

    def _parse_v2(self, file_path: str) -> str:
        """Tenta API v2 do marker-pdf."""
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered = converter(file_path)
        return rendered.markdown or ""