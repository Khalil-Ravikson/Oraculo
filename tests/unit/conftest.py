import pytest
from typing import List, Dict, Any, Type
from pydantic import BaseModel

# Importamos as tuas interfaces (Ports) e classes de domínio.
#
# 2026-09-09 (item A8c): `FakeVectorStore`/`IVectorStorePort` saíram junto com
# a cadeia de use-cases de RAG que ninguém chamava. O caminho vivo de busca é
# `redis_client.busca_hibrida()`, com RRF calculado à mão — quem quiser um
# dublê de busca deve mockar essa função, não um port que não existe mais.
from src.domain.ports.llm_Provider import ILLMProvider, LLMResponse, T

# =====================================================================
# 1. Implementações "Fake" (Adaptadores de Teste Isolados)
# =====================================================================

class FakeLLMProvider(ILLMProvider):
    """
    Simulador de LLM (Gemini/Groq) assíncrono.
    Permite injetar respostas fixas para testes determinísticos,
    sem gastar tokens ou depender de internet.
    """
    def __init__(self):
        self.respostas_texto = {}
        self.respostas_estruturadas = {}
        self.ultima_chamada = None

    async def gerar_resposta_async(
        self,
        prompt: str,
        system_instruction: str = "",
        temperatura: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.ultima_chamada = prompt
        
        # Pega a resposta programada no teste ou um valor padrão
        conteudo = self.respostas_texto.get("default", "Esta é uma resposta simulada pela IA.")
        
        return LLMResponse(
            conteudo=conteudo,
            model="fake-llm-v1",
            input_tokens=10,
            output_tokens=25,
            sucesso=True
        )

    async def gerar_resposta_estruturada_async(
        self,
        prompt: str,
        response_schema: Type[T],
        system_instruction: str = "",
        temperatura: float = 0.0,
    ) -> T | None:
        self.ultima_chamada = prompt
        
        # Para testes estruturados, o teste deve configurar um dicionário
        # de dados falsos que se alinhe com o Schema Pydantic esperado.
        schema_name = response_schema.__name__
        dados_falsos = self.respostas_estruturadas.get(schema_name)
        
        if dados_falsos is None:
            # Se o teste esqueceu de configurar, levanta erro claro
            raise ValueError(f"Configure 'respostas_estruturadas' para o schema {schema_name} no FakeLLMProvider.")
        
        # Instancia o objeto Pydantic e devolve (O tal 'Global' T que mencionaste)
        return response_schema(**dados_falsos)


@pytest.fixture
def fake_llm():
    """Injeta um provedor de LLM falso e limpo para cada teste."""
    return FakeLLMProvider()

