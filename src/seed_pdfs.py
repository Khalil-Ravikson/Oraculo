import asyncio
import os
import sys

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(current_dir))

from src.rag.ingestion.pipeline import IngestionPipeline
from src.infrastructure.adapters.parsers.pymupdf_adapter import PyMuPDFAdapter
from src.rag.ingestion.chunker_factory import ChunkerFactory
from src.rag.embeddings import get_embeddings

async def main():
    files = [
        ("/app/dados/PDF/academicos/calendario-academico-2026.pdf", "calendario"),
        ("/app/dados/PDF/academicos/edital_paes_2026.pdf", "edital"),
        ("/app/dados/PDF/academicos/guia_contatos_2025.pdf", "contatos")
    ]
    for path, doc_type in files:
        if not os.path.exists(path):
            print(f"❌ Arquivo não encontrado: {path}")
            continue
        print(f"🚀 Iniciando ingestao de {path} (tipo: {doc_type})...")
        try:
            if doc_type == "calendario":
                from src.infrastructure.adapters.parsers.calendar_llm_adapter import CalendarLLMAdapter
                parser = CalendarLLMAdapter()
            else:
                parser = PyMuPDFAdapter()
                
            chunker = ChunkerFactory.for_doc_type(doc_type)
            embeddings = get_embeddings()
            
            p = IngestionPipeline(parser=parser, chunker=chunker, embeddings=embeddings)
            res = await p.run_async(path, doc_type)
            if res.success:
                print(f"   ✅ Sucesso! Ingeridos {res.chunks_saved} chunks em {res.elapsed_ms}ms")
            else:
                print(f"   ❌ Falha: {res.error}")
        except Exception as e:
            print(f"   ❌ Erro de exceção ao processar {path}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
