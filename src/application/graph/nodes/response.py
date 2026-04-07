from src.application.graph.state import OracleState

def respond_node(state: OracleState):
    """
    Último nó antes do END. Formata a resposta para o WhatsApp.
    """
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