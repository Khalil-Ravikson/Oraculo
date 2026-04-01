import pytest
from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase
from src.domain.ports.vector_store_port import IVectorStorePort
from src.rag.query_transform import QueryTransformada

# 1. Dublê do Banco Vetorial (Mock)
class MockVectorStore(IVectorStorePort):
    async def salvar_chunks(self, chunks: list) -> None:
        pass

    async def buscar_hibrido(self, query_text: str, k_vector: int, k_text: int, source_filter: str = None):
        # Simulamos que o banco encontrou este pedaço de texto perdido lá dentro
        return [
            {
                "content": "EVENTO: Matrícula de veteranos | DATA: 03/02/2026",
                "source": "calendario-academico-2026.pdf",
                "doc_type": "calendario",
                "rrf_score": 0.95
            }
        ]

# 2. O Teste (A.A.A)
@pytest.mark.asyncio
async def test_recuperacao_e_formatacao_hierarquica():
    # Arrange (Preparação)
    mock_db = MockVectorStore()
    use_case = RetrieveContextUseCase(vector_store=mock_db)

    # Fingimos que a query já passou pelo LLM e foi transformada
    query_mock = QueryTransformada(
        query_original="quando é a matrícula?",
        query_principal="matrícula veteranos 2026",
        sub_queries=[],
        foi_transformada=True
    )

    # Act (Ação)
    resultado = await use_case.executar(query_mock, doc_type="calendario")

    # Assert (Verificação)
    assert resultado.encontrou is True
    
    # Verifica se a regra de Domínio construiu o cabeçalho corretamente!
    assert "━━━ FONTE: Calendário Acadêmico UEMA 2026 [CALENDÁRIO ACADÊMICO] ━━━" in resultado.contexto_formatado
    assert "EVENTO: Matrícula de veteranos" in resultado.contexto_formatado