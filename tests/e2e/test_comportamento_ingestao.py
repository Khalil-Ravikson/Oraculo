import pytest
from src.rag.embeddings import get_embeddings
# Ajuste o import abaixo para a classe exata que você usa para chunking em src/rag/ingestion.py
# (Geralmente é o RecursiveCharacterTextSplitter do Langchain ou similar)
from langchain_text_splitters import RecursiveCharacterTextSplitter

@pytest.mark.asyncio
async def test_geracao_de_embeddings_e_consumo_de_api():
    """Garante que o modelo de embeddings (Gemini/OpenAI) está respondendo e gerando o vetor certo."""
    texto_teste = "A UEMA oferece o curso de Engenharia de Computação no campus de São Luís."
    
    # 1. Chama a sua função real de embeddings
    embeddings_model = get_embeddings()
    vetor = embeddings_model.embed_query(texto_teste)
    
    # 2. Verificações
    assert vetor is not None, "A API de embeddings não retornou nada!"
    assert len(vetor) > 0, "O vetor veio vazio!"
    
    # Imprime no terminal o tamanho do vetor (ex: 768 para Google, 1536 para OpenAI)
    # Isso ajuda a ter clareza pro seu dashboard depois!
    print(f"\n[INFO] Tamanho do Embedding gerado: {len(vetor)} dimensões.")


def test_comportamento_do_chunking_de_documentos():
    """Mostra exatamente como o seu sistema está fatiando os PDFs."""
    # Simulando um texto de PDF extraído (Edital com regras e datas misturadas)
    texto_pdf = (
        
        """ 
        Especificação Técnica: Agente RAG Académico UEMA v5
        **Documento:** Especificação Interna de Desenvolvimento  
        **Versão:** 5.0
        **Data:** Março de 2026  
        **Autor:** CTIC/UEMA — Centro de Tecnologia da Informação e Comunicação  
        **Classificação:** Teste Interno — RAG Evaluation Dataset"

        ---

        ## Resumo Executivo

        O **Bot UEMA** é um assistente virtual académico acessível via WhatsApp, desenvolvido para atender alunos, coordenadores e administradores da Universidade Estadual do Maranhão. O sistema utiliza arquitetura RAG (Retrieval-Augmented Generation) com busca híbrida (vectorial + BM25) sobre documentos académicos indexados no Redis Stack.

        Este documento serve como **caso de teste primário** para o pipeline RAG, contendo tabelas estruturadas, siglas técnicas e informações hierárquicas que permitem validar a qualidade da ingestão e da recuperação.

        ---

        ## 1. Arquitectura do Sistema

        ### 1.1 Stack Tecnológico

        | Componente | Tecnologia | Função |
        |---|---|---|
        | LLM | Gemini 2.0 Flash | Geração de respostas |
        | Embedding | BAAI/bge-m3 (1024 dims) | Vetorização de texto |
        | Vector Store | Redis Stack (HNSW) | Busca vectorial |
        | BM25 | RediSearch nativo | Busca por keywords exactas |
        | Fusão | RRF (Reciprocal Rank Fusion) | Combina vectorial + BM25 |
        | Queue | Celery + Redis | Processamento assíncrono |
        | Gateway | Evolution API v2.3 | WhatsApp integration |
        """ * 10 # Multiplicando para gerar um texto maior
    )  
    
    # Aqui usamos as regras de corte que você configurou no seu projeto
    # Ajuste o chunk_size e overlap para os mesmos valores que você usa em src/rag/ingestion.py
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=450, 
        chunk_overlap=40
    )
    
    chunks = splitter.split_text(texto_pdf)
    
    # Verificações
    assert len(chunks) > 1, "O texto não foi dividido corretamente!"
    
    print("\n[INFO] --- ANÁLISE DE CHUNKS ---")
    print(f"Texto original tinha {len(texto_pdf)} caracteres.")
    print(f"Foi dividido em {len(chunks)} chunks.")
    
    # Vamos ver como a IA vai ler o primeiro e o último chunk (isso vai ditar se ela alucina ou não)
    print(f"\nCHUNK 0 (O que vai pro Redis e depois pra LLM): \n'{chunks[0]}'")
    print(f"\nCHUNK 1 (Olhe o overlap aqui): \n'{chunks[1]}'")