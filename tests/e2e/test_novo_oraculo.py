import asyncio
import sys
import os
import hashlib
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
load_dotenv()

# O GPS do Python para achar a src
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase

# Criamos um "Dublê" (Mock) para fingir que o LLM já transformou a pergunta
class MockQueryTransformada:
    def __init__(self, original: str, principal: str):
        self.query_original = original
        self.query_principal = principal
        self.sub_queries = []

async def testar():
    print("🚀 INICIANDO TESTE DA ARQUITETURA LIMPA...")
    adapter = RedisVectorAdapter()
    use_case = RetrieveContextUseCase(adapter)

    # --- 1. INGESTÃO ---
    print("\n📦 Injetando dados de teste...")
    teste_chunk = [{
        "chunk_id": hashlib.md5(b"mock_123").hexdigest()[:16],
        "content": "O CTIC da UEMA fica localizado no prédio administrativo central, ramal 2020.",
        "source": "guia_contatos_2025.pdf",
        "doc_type": "contatos"
    }]
    await adapter.salvar_chunks(teste_chunk)
    
    # Aguarda o RediSearch organizar a prateleira
    await asyncio.sleep(2)
    
    # --- 2. RECUPERAÇÃO ---
    print("\n🧠 Iniciando busca com RRF (No Caso de Uso)...")
    
    # Em vez de mandar uma string crua, instanciamos o objeto que o contrato exige!
    query_mock = MockQueryTransformada(
        original="Onde fica o CTIC?",
        principal="localização prédio CTIC UEMA"
    )
    
    # Passamos o objeto
    resultado = await use_case.executar(query_transformada=query_mock)
    
    # Note que agora lemos do atributo `.chunks` do ResultadoRecuperacao
    print(f"\n✅ Resultados encontrados: {len(resultado.chunks)}")
    for i, res in enumerate(resultado.chunks):
        print(f"\nTop {i+1} | Score RRF: {res.rrf_score:.4f}")
        print(f"Fonte: {res.titulo_fonte}")
        print(f"Conteúdo: {res.content}")

if __name__ == "__main__":
    asyncio.run(testar())