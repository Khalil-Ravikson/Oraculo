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
    """Nó 1: Busca documentos (Redis Assíncrono) e chama o LLM (Gemini)."""
    from src.application.graph.prompts import montar_prompt_geracao, SYSTEM_UEMA
    from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
    from src.application.use_cases.retrieve_context_use_case import RetrieveContextUseCase
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import SystemMessage, HumanMessage

    # Mock temporário para a QueryTransformada (até plugar o nó de transformação real)
    class MockQueryTransformada:
        def __init__(self, original: str, principal: str):
            self.query_original = original
            self.query_principal = principal
            self.sub_queries = []

    logger.info("🔍 [Nó: RAG] Iniciando busca vetorial com RRF...")
    pergunta = state["current_input"]

    # 1. Recuperação Real (Acionando a Clean Architecture)
    adapter = RedisVectorAdapter()
    use_case = RetrieveContextUseCase(adapter)
    
    query = MockQueryTransformada(original=pergunta, principal=pergunta)
    resultado_rag = await use_case.executar(query_transformada=query)
    
    contexto_recuperado = resultado_rag.contexto_formatado
    logger.info(f"✅ [Nó: RAG] {len(resultado_rag.chunks)} chunks recuperados!")

    # 2. Injeção de Identidade (Contexto do Aluno)
    ctx = state.get("user_context", {})
    contexto_usuario = f"Aluno: {state.get('user_name', 'Estudante')} | Curso: {ctx.get('curso', '?')} | Período: {ctx.get('periodo', '?')}" if ctx else ""
    
    # 3. Montagem do Prompt (Usando a sua função impecável)
    prompt_conteudo = montar_prompt_geracao(
        pergunta=pergunta,
        contexto_rag=contexto_recuperado,
        perfil_usuario=contexto_usuario
    )
    
    # 4. Chamada ao Provider (Gemini 2.5 Flash)
    logger.info("🧠 [Nó: RAG] Invocando o Gemini com o contexto formatado...")
    llm = ChatGoogleGenerativeAI(model="gemini-3-flash-preview", temperature=0.2)
    
    # Unindo o seu System Prompt com o conteúdo gerado
    mensagens = [
        SystemMessage(content=SYSTEM_UEMA),
        HumanMessage(content=prompt_conteudo)
    ]
    
    resposta_llm = await llm.ainvoke(mensagens)
    logger.info("✅ [Nó: RAG] Resposta gerada com sucesso!")
# 🧹 O FILTRO DEFINITIVO: Extraindo apenas o texto puro do Gemini
    conteudo_final = resposta_llm.content
    
    # Se o conteúdo vier como lista, pega o texto do primeiro elemento
    if isinstance(conteudo_final, list) and len(conteudo_final) > 0:
        primeiro_bloco = conteudo_final[0]
        if isinstance(primeiro_bloco, dict) and "text" in primeiro_bloco:
            conteudo_final = primeiro_bloco["text"]
            
    # Se o conteúdo vier como string simulando dicionário/lista (o seu caso!)
    elif isinstance(conteudo_final, str) and conteudo_final.strip().startswith("[{"):
        import ast
        try:
            blocos = ast.literal_eval(conteudo_final)
            if blocos and isinstance(blocos[0], dict) and "text" in blocos[0]:
                conteudo_final = blocos[0]["text"]
        except Exception as e:
            logger.warning(f"⚠️ Erro ao avaliar a string estruturada: {e}")
            
    # Se tudo falhar e ele for apenas uma string normal, já está em `conteudo_final`
    # 5. Atualiza o OracleState
    return {
        "final_response": str(conteudo_final), # Força para que seja sempre String,
        "rag_context": contexto_recuperado
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