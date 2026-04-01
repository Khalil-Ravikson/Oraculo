import pytest
import asyncio
from src.application.use_cases.ingest_document_use_case import IngestDocumentUseCase
from src.domain.ports.document_parser import IDocumentParser
from src.domain.ports.vector_store_port import IVectorStorePort

# 1. Criando Mocks (Falsos) para simular a Infraestrutura
class MockParser(IDocumentParser):
    def parse(self, file_path: str, instruction: str = "") -> str:
        # Finge que leu um PDF de Edital perfeitamente
        return "Tabela de Vagas PAES 2026\n\nEngenharia Civil | AC: 40 | PcD: 2"

class MockVectorStore(IVectorStorePort):
    def __init__(self):
        self.chunks_salvos = []

    async def salvar_chunks(self, chunks: list) -> None:
        self.chunks_salvos.extend(chunks)

    async def buscar_hibrido(self, query: str, k_vector: int, k_text: int, source_filter: str = None):
        return []

# 2. O Teste Unitário
@pytest.mark.asyncio
async def test_ingestao_de_documento_com_sucesso():
    # Arrange (Preparação)
    mock_parser = MockParser()
    mock_vector_store = MockVectorStore()
    
    use_case = IngestDocumentUseCase(parser=mock_parser, vector_store=mock_vector_store)
    
    config = {
        "doc_type": "edital",
        "label": "EDITAL PAES TESTE",
        "chunk_size": 200,
        "overlap": 20
    }

    # Act (Ação)
    qtd_salvos = await use_case.executar("caminho/falso/arquivo.pdf", "edital_teste.pdf", config)

    # Assert (Verificação)
    assert qtd_salvos > 0
    assert len(mock_vector_store.chunks_salvos) == qtd_salvos
    
    # Verifica se a regra de negócio do cabeçalho anti-alucinação foi aplicada
    primeiro_chunk = mock_vector_store.chunks_salvos[0]
    assert "[EDITAL PAES TESTE | edital]" in primeiro_chunk["content"]
    assert "Engenharia Civil | AC: 40" in primeiro_chunk["content"]
    assert primeiro_chunk["source"] == "edital_teste.pdf"