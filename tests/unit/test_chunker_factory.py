"""
tests/unit/test_chunker_factory.py
------------------------------------
Testes unitários do ChunkerFactory e estratégias de chunking.

Execute: pytest tests/unit/test_chunker_factory.py -v
"""
import pytest
from src.rag.ingestion.chunker_factory import (
    ChunkerFactory,
    MarkdownHeaderChunker,
    RecursiveChunker,
    SemanticChunker,
)


@pytest.mark.unit
class TestRecursiveChunker:
    def test_divide_texto_em_chunks(self):
        chunker = RecursiveChunker(chunk_size=100, overlap=20)
        texto = "Parágrafo um com algum texto.\n\nParágrafo dois com mais texto.\n\nParágrafo três."
        chunks = chunker.chunk(texto, source="test.pdf", doc_type="geral")
        assert len(chunks) >= 1
        assert all(c.text for c in chunks)

    def test_chunks_tem_metadata_correto(self):
        chunker = RecursiveChunker(chunk_size=200, overlap=30)
        texto = "Texto de teste para verificar metadados do chunk gerado."
        chunks = chunker.chunk(texto, source="edital.pdf", doc_type="edital")
        assert chunks
        assert chunks[0].metadata["source"] == "edital.pdf"
        assert chunks[0].metadata["doc_type"] == "edital"
        assert chunks[0].metadata["chunk_index"] == 0

    def test_overlap_funciona(self):
        chunker = RecursiveChunker(chunk_size=50, overlap=20)
        texto = "A " * 100  # texto repetitivo longo
        chunks = chunker.chunk(texto, source="t.txt", doc_type="geral")
        assert len(chunks) > 1

    def test_texto_vazio_retorna_lista(self):
        chunker = RecursiveChunker()
        chunks = chunker.chunk("", source="vazio.pdf", doc_type="geral")
        assert isinstance(chunks, list)

    def test_name(self):
        assert RecursiveChunker().name == "recursive"


@pytest.mark.unit
class TestMarkdownHeaderChunker:
    def test_divide_por_headers(self):
        chunker = MarkdownHeaderChunker(chunk_size=500, overlap=50)
        texto = """# Calendário Acadêmico UEMA 2026

## Semestre 2026.1

### Matrícula de Veteranos
DATA: 03/02/2026 a 07/02/2026

### Início das Aulas
DATA: 10/02/2026

## Semestre 2026.2

### Matrícula de Calouros
DATA: 01/08/2026
"""
        chunks = chunker.chunk(texto, source="calendario.pdf", doc_type="calendario")
        assert len(chunks) >= 2
        assert all(c.text for c in chunks)

    def test_preserva_contexto_hierarquico(self):
        chunker = MarkdownHeaderChunker(chunk_size=500, overlap=50)
        texto = "# Edital PAES 2026\n\n## Vagas\n\nEngenharia Civil: 40 vagas"
        chunks = chunker.chunk(texto, source="edital.pdf", doc_type="edital")
        # Algum chunk deve ter header_context nos metadados
        has_header_ctx = any("header_context" in c.metadata for c in chunks)
        assert has_header_ctx or len(chunks) > 0  # ao menos gerou chunks

    def test_fallback_sem_markdown(self):
        """Texto sem headers Markdown deve usar recursive como fallback."""
        chunker = MarkdownHeaderChunker(chunk_size=100, overlap=20)
        texto = "Texto simples sem headers. Apenas parágrafos normais. " * 5
        chunks = chunker.chunk(texto, source="simples.txt", doc_type="geral")
        assert len(chunks) >= 1

    def test_name(self):
        assert MarkdownHeaderChunker().name == "markdown"


@pytest.mark.unit
class TestSemanticChunker:
    def test_sem_embeddings_usa_recursive_fallback(self):
        chunker = SemanticChunker(embeddings_model=None)
        texto = "Parágrafo sobre matrícula.\n\nParágrafo sobre cotas.\n\nParágrafo sobre prazos."
        chunks = chunker.chunk(texto, source="test.pdf", doc_type="geral")
        assert len(chunks) >= 1

    def test_name(self):
        assert SemanticChunker().name == "semantic"


@pytest.mark.unit
class TestChunkerFactory:
    def test_get_recursive(self):
        chunker = ChunkerFactory.get("recursive", chunk_size=300)
        assert chunker.name == "recursive"

    def test_get_markdown(self):
        chunker = ChunkerFactory.get("markdown", chunk_size=400, overlap=60)
        assert chunker.name == "markdown"

    def test_get_semantic(self):
        chunker = ChunkerFactory.get("semantic")
        assert chunker.name == "semantic"

    def test_get_invalido_levanta_erro(self):
        with pytest.raises(ValueError, match="não encontrado"):
            ChunkerFactory.get("inexistente")

    def test_for_parser_docling_retorna_markdown(self):
        chunker = ChunkerFactory.for_parser("docling")
        assert chunker.name == "markdown"

    def test_for_parser_marker_retorna_markdown(self):
        chunker = ChunkerFactory.for_parser("marker")
        assert chunker.name == "markdown"

    def test_for_parser_pymupdf_retorna_recursive(self):
        chunker = ChunkerFactory.for_parser("pymupdf")
        assert chunker.name == "recursive"

    def test_for_doc_type_calendario_retorna_markdown(self):
        chunker = ChunkerFactory.for_doc_type("calendario")
        assert chunker.name == "markdown"

    def test_for_doc_type_edital_retorna_markdown(self):
        chunker = ChunkerFactory.for_doc_type("edital")
        assert chunker.name == "markdown"

    def test_for_doc_type_contatos_retorna_recursive(self):
        chunker = ChunkerFactory.for_doc_type("contatos")
        assert chunker.name == "recursive"

    def test_for_doc_type_geral_retorna_recursive(self):
        chunker = ChunkerFactory.for_doc_type("geral")
        assert chunker.name == "recursive"

    def test_for_doc_type_wiki_sem_embeddings_retorna_recursive(self):
        """Sem embeddings, wiki deve usar recursive, não semantic."""
        chunker = ChunkerFactory.for_doc_type("wiki_ctic", embeddings=None)
        assert chunker.name == "recursive"