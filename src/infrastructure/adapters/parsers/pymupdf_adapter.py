# src/infrastructure/adapters/parsers/pymupdf_adapter.py
import logging
from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

class PyMuPDFAdapter(IDocumentParser):
    """Adapter para extração de texto rápida e local usando PyMuPDF (fitz)."""

    def parse(self, file_path: str, instruction: str = "") -> str:
        try:
            import fitz  # Import isolado para não quebrar a app se a lib não existir
            
            doc = fitz.open(file_path)
            paginas = [p.get_text("text") for p in doc if p.get_text("text").strip()]
            doc.close()

            if not paginas:
                logger.warning(f"⚠️ PyMuPDF: Nenhuma página com texto extraível em '{file_path}'. (É um scan?)")
                return ""
                
            logger.debug(f"📄 PyMuPDF: {len(paginas)} páginas extraídas com sucesso.")
            return "\n\n".join(paginas)

        except ImportError:
            logger.error("❌ A biblioteca 'pymupdf' não está instalada.")
            raise
        except Exception as e:
            logger.exception(f"❌ Erro no PyMuPDF ao processar '{file_path}': {e}")
            return ""