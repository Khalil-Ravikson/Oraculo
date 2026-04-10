import asyncio
from src.application.graph.state import OracleState
"""
async def respond_node(state: OracleState):
    
    Último nó antes do END. Formata a resposta para o WhatsApp.
    
    messages = state.get("messages", [])
    if not messages:
        return {"final_response": "Desculpe, não consegui processar sua solicitação."}
        
    last_message = messages[-1]
    content = last_message.content

    # Se o conteúdo estiver vazio (comum em tool_calls), pegamos a penúltima ou feedback
    if not content and len(messages) > 1:
        content = "Processando sua solicitação..."

    return {
        "final_response": content,
        "pending_confirmation": False
    }


"""

async def respond_node(state: OracleState):
    """
    Último nó antes do END. Garante que sempre haja uma string no final_response.
    """
    # 1. Tenta pegar a resposta final já mastigada
    content = state.get("final_response")
    
    # 2. Se não existir, pega a última mensagem do histórico (AIMessage)
    if not content:
        messages = state.get("messages", [])
        if messages:
            content = messages[-1].content

    # 3. Fallback absoluto
    if not content or content == "Desculpe, tive dificuldade em formular a resposta.":
        content = "Não encontrei informações específicas sobre isso nos editais. Posso tentar ajudar com outra dúvida?"

    return {
        "final_response": content,
        "pending_confirmation": False
    }