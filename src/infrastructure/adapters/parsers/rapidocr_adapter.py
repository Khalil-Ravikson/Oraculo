"""
src/infrastructure/adapters/parsers/rapidocr_adapter.py
-------------------------------------------------------
Adapter para extração de texto via OCR usando rapidocr-onnxruntime.
"""
from __future__ import annotations
import logging
import os
import numpy as np
from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

class RapidOcrAdapter(IDocumentParser):
    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # Inicializa de forma preguiçosa (lazy load) para economizar RAM no startup
            self._engine = RapidOCR()
        return self._engine

    def parse(self, file_path: str, instruction: str = "") -> str:
        if not os.path.exists(file_path):
            logger.error("❌ [OCR PARSER] Arquivo não encontrado: %s", file_path)
            return ""

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".pdf",):
            return self._parse_pdf(file_path)
        else:
            return self._parse_image(file_path)

    def _parse_image(self, file_path: str) -> str:
        try:
            logger.info("📸 [OCR PARSER] Processando imagem: %s", os.path.basename(file_path))
            engine = self._get_engine()
            res, elapse = engine(file_path)
            if not res:
                return ""
            # O retorno do RapidOCR é uma lista de: [[box, text, score], ...]
            return "\n".join(line[1] for line in res if line[1])
        except Exception as e:
            logger.exception("❌ [OCR PARSER] Falha na imagem '%s': %s", file_path, e)
            return ""

    def _parse_pdf(self, file_path: str) -> str:
        try:
            import fitz
            logger.info("📸 [OCR PARSER] Processando PDF via OCR: %s", os.path.basename(file_path))
            doc = fitz.open(file_path)
            pages_text = []
            engine = self._get_engine()

            for page_num, page in enumerate(doc):
                # Renderiza a página como imagem (pixmap)
                # dpi=150 é um balanço excelente de precisão e velocidade
                pix = page.get_pixmap(dpi=150)
                if pix.n == 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                
                img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
                res, elapse = engine(img_np)
                
                if res:
                    page_text = "\n".join(line[1] for line in res if line[1])
                    if page_text:
                        pages_text.append(page_text)
                        
            doc.close()
            return "\n\n".join(pages_text)
        except Exception as e:
            logger.exception("❌ [OCR PARSER] Falha no PDF '%s': %s", file_path, e)
            return ""
