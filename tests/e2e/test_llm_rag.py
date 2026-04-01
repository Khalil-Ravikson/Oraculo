import asyncio
import sys
import os
import time  # <-- O Cronômetro
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
load_dotenv()

from src.application.graph.nodes import node_rag
from src.application.graph.state import OracleState

async def testar_cerebro_completo():
    print("🚀 Iniciando o Teste Definitivo do Oráculo (RAG + Gemini 3 Flash)...\n")
    
    pergunta_teste = "Olá! Qual é o email do suporte técnico e onde o prédio deles fica localizado?"
    
    estado_simulado: OracleState = {
        "current_input": pergunta_teste,
        "user_name": "Khalil",
        "user_context": {"curso": "Engenharia de Computação", "periodo": "11º"},
        "user_phone": "98999999999", "user_id": "123", "user_role": "student",
        "user_status": "active", "messages": [], "route": "rag",
        "tool_name": None, "tool_args": None, "pending_confirmation": None,
        "confirmation_result": None, "final_response": None, "rag_context": None, "crag_score": 0.0
    }
    
    print(f"👤 Aluno simulado: {estado_simulado['user_name']} ({estado_simulado['user_context']['curso']})")
    print(f"🗣️ Pergunta: '{pergunta_teste}'\n")
    
    # ⏱️ DISPARANDO O CRONÔMETRO
    inicio_timer = time.perf_counter()
    
    print("⏳ [1/2] O Nó RAG está buscando no Redis com RRF...")
    print("🧠 [2/2] O Nó RAG está enviando o contexto para o Gemini pensar...")
    
    resultado = await node_rag(estado_simulado)
    
    # ⏱️ PARANDO O CRONÔMETRO
    fim_timer = time.perf_counter()
    tempo_total = fim_timer - inicio_timer
    
    print("\n" + "="*70)
    print("📚 CONTEXTO ENTREGUE AO GEMINI:")
    print("="*70)
    print(resultado.get("rag_context"))
    
    print("\n" + "="*70)
    print(f"🤖 RESPOSTA FINAL DO ORÁCULO (Tempo de Resposta: {tempo_total:.2f} segundos):")
    print("="*70)
    print(resultado.get("final_response"))
    print("="*70)

if __name__ == "__main__":
    asyncio.run(testar_cerebro_completo())