"""
PyMuPDFAdapter — extração com formatação Markdown básica.
Detecta tamanhos de fonte para inferir hierarquia H1/H2/H3.
Preserva quebras lógicas entre blocos de texto.
"""
from __future__ import annotations
import logging
import os
from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)


class PyMuPDFAdapter(IDocumentParser):

    def parse(self, file_path: str, instruction: str = "") -> str:
        try:
            import fitz
        except ImportError:
            logger.error("❌ pymupdf não instalado.")
            raise

        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""

        try:
            doc = fitz.open(file_path)
            pages_md = []

            for page in doc:
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
                page_lines = []

                for block in blocks:
                    if block.get("type") != 0:  # type 0 = texto
                        continue

                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        if not spans:
                            continue

                        # Pega o maior tamanho de fonte na linha
                        max_size = max(s.get("size", 0) for s in spans)
                        text = " ".join(s.get("text", "").strip() for s in spans).strip()

                        if not text:
                            continue

                        # Inferência de hierarquia por tamanho de fonte
                        # Calibrado para PDFs acadêmicos típicos (body ~10-12pt)
                        if max_size >= 18:
                            page_lines.append(f"# {text}")
                        elif max_size >= 14:
                            page_lines.append(f"## {text}")
                        elif max_size >= 12.5:
                            page_lines.append(f"### {text}")
                        else:
                            page_lines.append(text)

                if page_lines:
                    pages_md.append("\n".join(page_lines))

            doc.close()

            if not pages_md:
                logger.warning("⚠️  PyMuPDF: nenhum texto extraível em '%s'", file_path)
                return ""

            result = "\n\n".join(pages_md)
            logger.debug("📄 PyMuPDF Markdown: %d chars de '%s'",
                         len(result), os.path.basename(file_path))
            return result

        except Exception as e:
            logger.exception("❌ PyMuPDF falhou para '%s': %s", file_path, e)
            return ""