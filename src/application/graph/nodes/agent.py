from langchain_google_genai import ChatGoogleGenerativeAI
from src.application.graph.state import OracleState
from src.domain.tools.tool_registry import ToolRegistry

def agent_node(state: OracleState, tool_registry: ToolRegistry):
    """
    O Cérebro: Decide qual ferramenta usar baseada no histórico e role.
    """
    # 1. Instancia o modelo (pode vir de settings)
    llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro", temperature=0)
    
    # 2. Filtra ferramentas permitidas para o user_role (STUDENT, ADMIN, etc)
    role = state.get("user_role", "guest")
    tools = tool_registry.get_tools_for_role(role)
    
    # 3. Faz o bind e invoca
    llm_with_tools = llm.bind_tools(tools)
    response = llm_with_tools.invoke(state["messages"])
    
    # Retornamos a mensagem do modelo para o estado
    return {"messages": [response]}