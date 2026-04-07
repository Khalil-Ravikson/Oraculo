from src.application.graph.state import OracleState

def ask_confirm_node(state: OracleState):
    """
    Prepara o estado para a interrupção do LangGraph.
    """
    return {
        "pending_confirmation": True,
        "final_response": "⚠️ *Ação Sensível detectada.*\nVocê confirma a execução desta operação? (Responda 'Sim' para prosseguir)"
    }