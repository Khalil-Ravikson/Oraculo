# src/infrastructure/adapters/parsers/llamaparse_adapter.py
import logging
from src.domain.ports.document_parser import IDocumentParser
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

class LlamaParseAdapter(IDocumentParser):
    """Adapter para extração de texto estruturado na nuvem usando LlamaParse."""

    def parse(self, file_path: str, instruction: str = "") -> str:
        if not settings.LLAMA_CLOUD_API_KEY:
            logger.error("❌ LLAMA_CLOUD_API_KEY ausente. Não é possível usar o LlamaParse.")
            return ""

        try:
            from llama_parse import LlamaParse
            
            # Instrução otimizada para manter o Markdown nativo das tabelas
            parsing_instruction = instruction or (
                "Extraia todo o texto com máxima precisão. "
                "Preserve rigorosamente a estrutura de tabelas usando a formatação padrão do Markdown. "
                "Mantenha títulos, subtítulos e listas estruturados. Responda em português."
            )

            parser = LlamaParse(
                api_key=settings.LLAMA_CLOUD_API_KEY,
                result_type="markdown",
                language="pt", # Se der warning de parametro desconhecido na nova versão, pode remover
                verbose=False,
                parsing_instruction=parsing_instruction, # <--- Ajustado aqui!
            )
            
            docs = parser.load_data(file_path)
            paginas = [doc.text for doc in docs if doc.text.strip()]
            
            logger.debug(f"🦙 LlamaParse: {len(paginas)} páginas extraídas com sucesso.")
            return "\n\n".join(paginas)

        except ImportError:
            logger.error("❌ A biblioteca 'llama-parse' não está instalada.")
            raise
        except Exception as e:
            logger.exception(f"❌ Erro no LlamaParse ao processar '{file_path}': {e}")
            return ""