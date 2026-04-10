import pytest
import httpx
import json

# URL do seu container ou local onde o Uvicorn está rodando
BASE_URL = "http://localhost:9000" 

@pytest.mark.asyncio
async def test_debug_rag_error():
    """
    Testa o endpoint de chat e força a exibição do erro real.
    """
    payload = {
        "message": "Qual é o email do suporte técnico?"
    }
    
    print("\n--- 🔍 INICIANDO DEBUG NO SERVIDOR UVICORN ---")
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # 1. Tenta enviar a mensagem
        response = await client.post("/hub/chat/send", json=payload)
        
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Resposta Bruta: {json.dumps(data, indent=2)}")

        # 2. Se a resposta for a mensagem de erro genérica, algo no Grafo falhou
        if data.get("response") == "Desculpe, tive dificuldade em formular a resposta.":
            pytest.fail("❌ O Oráculo caiu no fallback de erro silencioso!")
        
        assert response.status_code == 200
        assert "email" in data.get("response", "").lower()