import logging
import re
from langchain_core.messages import AIMessage
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

async def node_classify(state: OracleState) -> dict:
    """Nó 0: Define a rota sem chamar o LLM principal (economia de tokens)."""
    msg = state["current_input"].strip().lower()

    # Retomada HITL
    if state.get("pending_confirmation"):
        if msg in ("sim", "s", "yes", "y", "confirmo", "ok"):
            return {"confirmation_result": "confirmed"}
        if msg in ("não", "nao", "n", "no", "cancelar"):
            return {
                "confirmation_result": "cancelled",
                "final_response": "❌ Operação cancelada. Como mais posso ajudar?",
                "route": "respond_only",
            }
        return {
            "final_response": f"{state['pending_confirmation']}\n\nResponda *SIM* para confirmar ou *NÃO* para cancelar.",
            "route": "respond_only",
        }

    # TODO: Plugar seu Roteador Semântico do Redis aqui
    # Por enquanto, roteamento básico para testar o fluxo
    _CRUD_INTENT = re.compile(r"(atualiz|mudar|alterar|corrig|trocar).{0,30}(nome|email|telefone|matrícula|curso)", re.IGNORECASE)
    
    if _CRUD_INTENT.search(msg):
        # Simula a extração de argumentos da tool
        return {"route": "crud", "tool_name": "update_student_data", "tool_args": {"campo": "email"}}
    elif len(msg.split()) <= 3 and any(w in msg for w in ["oi", "olá", "ola", "bom dia"]):
        return {"route": "greeting"}
    
    return {"route": "rag", "tool_name": None, "tool_args": None}


async def node_rag(state: OracleState) -> dict:
    """Nó 1: Busca documentos e chama o LLM usando os novos prompts Few-Shot."""
    # Importação apontando para o lugar correto na Clean Architecture
    from src.application.graph.prompts import montar_prompt_geracao, SYSTEM_UEMA
    
    # 1. Recuperação (Mockada até plugar o Redis)
    contexto_recuperado = "Contexto recuperado do RedisHNSW virá aqui."
    
    # 2. Injeção de Identidade (Regra 2)
    ctx = state.get("user_context", {})
    contexto_usuario = f"Aluno: {state['user_name']} | Curso: {ctx.get('curso', '?')} | Período: {ctx.get('periodo', '?')}" if ctx else ""
    
    # 3. Montagem do Prompt
    prompt = montar_prompt_geracao(
        pergunta=state["current_input"],
        contexto_rag=contexto_recuperado,
        perfil_usuario=contexto_usuario
    )
    
    # TODO: Chamar Provider (Gemini) real
    resposta_llm = "Resposta gerada pelo LLM baseada nos documentos (Mock)."
    
    return {
        "final_response": resposta_llm,
        "rag_context": contexto_recuperado
    }

async def node_ask_confirm(state: OracleState) -> dict:
    """Nó 2: Prepara a pergunta para o Human-in-the-Loop."""
    tool = state.get("tool_name", "ação")
    pergunta = (
        f"⚠️ Você solicitou uma alteração no sistema ({tool}).\n\n"
        f"Deseja prosseguir?\nResponda *SIM* para confirmar ou *NÃO* para cancelar."
    )
    return {
        "pending_confirmation": pergunta,
        "confirmation_result": "pending",
        "final_response": pergunta,
    }

async def node_exec_tool(state: OracleState) -> dict:
    """Nó 3: Roda a ferramenta após o 'SIM' do aluno."""
    tool = state.get("tool_name")
    logger.info(f"Executando tool {tool} no banco...")
    return {
        "final_response": f"✅ Sucesso! A ação `{tool}` foi executada.",
        "pending_confirmation": None,
        "confirmation_result": None,
    }

async def node_greeting(state: OracleState) -> dict:
    """Nó 4: Saudação rápida e barata."""
    from src.application.use_cases.messages import MSG_BOAS_VINDAS_USUARIO
    nome = state["user_name"].split()[0]
    return {"final_response": MSG_BOAS_VINDAS_USUARIO.format(nome=nome)}

async def node_respond(state: OracleState) -> dict:
    """Nó 5: Apenas finaliza e registra."""
    return {}