import asyncio
import sys
import os
from dotenv import load_dotenv

# Garante que o Python ache a pasta src
sys.path.append(os.getcwd())
load_dotenv()

from src.application.graph.nodes.core import OraculoCoreNodes
from langchain_core.messages import HumanMessage

async def testar_cerebro_completo():
    print("🚀 Iniciando Teste do Oráculo (Modo Classe)...\n")
    
    # 1. Instanciamos os nós (Passamos None no router pois não vamos usar o roteador neste teste)
    nodes = OraculoCoreNodes(oraculo_router=None)
    
    pergunta = "Qual é o email do suporte técnico da UEMA?"
    
    # 2. Estado simulado (OracleState)
    estado = {
        "messages": [HumanMessage(content=pergunta)],
        "user_name": "Khalil",
        "curso": "Engenharia de Computação",
        "rag_context": "O email do CTIC é suporte@uema.br e fica no prédio da reitoria.", # Contexto fake para testar o Gemini
    }
    
    print(f"🧠 Enviando para o Gemini...")
    
    try:
        # 3. CHAMADA DIRETA AO NÓ (Ajuste o nome do método se for node_generate ou node_rag)
        resultado = await nodes.node_generate(estado)
        
        print("\n" + "="*50)
        print("🤖 RESPOSTA FINAL:")
        print(resultado.get("final_response"))
        print("="*50)
        
    except Exception as e:
        print(f"\n❌ OCORREU UM ERRO: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(testar_cerebro_completo())