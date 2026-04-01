import asyncio
import sys
import os
import hashlib
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
from src.infrastructure.redis_client import criar_indice_chunks, get_redis, IDX_CHUNKS
from src.rag.embeddings import get_embeddings

async def testar_fluxo_rag():
    adapter = RedisVectorAdapter()

    # --- 0. RAIO-X DO GOOGLE ---
    print("\n--- 0. DIAGNÓSTICO DO GOOGLE ---")
    print("⏳ Perguntando ao Google o tamanho do vetor...")
    vetor_teste = await asyncio.to_thread(get_embeddings().embed_query, "teste")
    dimensoes = len(vetor_teste)
    print(f"📏 Tamanho exato gerado pelo Google: {dimensoes} dimensões")

    # --- 1. PREPARANDO O BANCO VETORIAL ---
    print("\n--- 1. PREPARANDO O BANCO VETORIAL ---")
    try:
        get_redis().ft(IDX_CHUNKS).dropindex(delete_documents=True)
    except Exception:
        pass
    
    await asyncio.to_thread(criar_indice_chunks)
    print("✅ Índice 'idx:rag:chunks' recriado!")

    # --- 2. INGESTÃO ---
    print("\n--- 2. INICIANDO INGESTÃO NO REDIS ---")
    chunks = [
        {
            "chunk_id": hashlib.md5(b"mock_vaga").hexdigest()[:16],
            "content": "[VAGAS MOCK PAES 2026 | edital]\nCURSO: Engenharia de Computação | TURNO: Integral | VAGAS AMPLA CONCORRÊNCIA: 30",
            "source": "vagas_mock_2026.csv",
            "doc_type": "edital",
            "metadata": {"chunk_index": 0}
        },
        {
            "chunk_id": hashlib.md5(b"mock_contato").hexdigest()[:16],
            "content": "[CONTATOS MOCK UEMA | contatos]\nSETOR: Suporte Técnico CTIC | EMAIL: ctic@uema.br | RAMAL: 2020",
            "source": "contatos_mock.csv",
            "doc_type": "contatos",
            "metadata": {"chunk_index": 1}
        }
    ]
    await adapter.salvar_chunks(chunks)
    
    print("⏳ Aguardando 2 segundos para o RediSearch organizar o catálogo...")
    await asyncio.sleep(2)

    # --- 3. RAIO-X DO REDIS ---
    print("\n--- 3. RAIO-X DO CATÁLOGO DO REDIS ---")
    info = get_redis().ft(IDX_CHUNKS).info()
    num_docs = info.get('num_docs', 0)
    falhas = info.get('hash_indexing_failures', 0)
    
    print(f"📦 Documentos catalogados com sucesso: {num_docs}")
    print(f"❌ Falhas matemáticas (rejeições silenciosas): {falhas}")

    if num_docs == 0:
        print("\n🚨 ALERTA VERMELHO: O Redis rejeitou os seus dados! O erro está na matemática (dimensões).")
        return

    # --- 4. BUSCA HÍBRIDA ---
    print("\n--- 4. INICIANDO BUSCA HÍBRIDA ---")
    pergunta = "Qual o email de contato do CTIC?"
    resultados = await adapter.buscar_hibrido(query_text=pergunta, k_vector=2, k_text=2)

    if not resultados:
        print("⚠️ O catálogo tem documentos, mas a busca não encontrou as palavras-chave.")
        return

    print(f"\n✅ {len(resultados)} resultados encontrados!")
    for i, res in enumerate(resultados):
        print(f"\nTop {i+1} | RRF Score: {res.get('rrf_score', 'N/A')}")
        print(f"Fonte: {res.get('source')} | Texto:\n{res.get('content')}")

if __name__ == "__main__":
    asyncio.run(testar_fluxo_rag())