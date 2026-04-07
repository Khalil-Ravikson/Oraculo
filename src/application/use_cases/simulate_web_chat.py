import logging
import uuid
import json
from src.application.graph.builder import get_compiled_graph
from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)

# Memória em RAM temporária para gerir as sessões do simulador
_simulador_sessions = {}

class SimulateWebChatUseCase:
    """
    Use Case com Observabilidade e Extração Direta de Memória.
    """
    
    async def executar(self, session_id: str, mensagem: str, admin_name: str) -> str:
        logger.info(f"\n{'='*50}\n🗣️ NOVA MENSAGEM WEB [User: {admin_name}]\n💬 Input: {mensagem}\n{'='*50}")
        
        if mensagem.strip().lower() == "/clear":
            _simulador_sessions[session_id] = str(uuid.uuid4())
            return "🧹 Sessão de simulação limpa. O histórico desta thread foi apagado."
            
        thread_id = _simulador_sessions.get(session_id, session_id)
        
        graph = get_compiled_graph()
        config = {"configurable": {"thread_id": thread_id}}
        
        identity_mock = {
            "user_id": thread_id,
            "nome": admin_name,
            "role": "admin",
            "is_admin": True,
            "body": mensagem
        }
        
        estado_input = OracleState.from_identity(identity_mock)
        
        try:
            logger.info(f"🚀 Iniciando execução do Grafo (Thread: {thread_id})...")
            
            # 1. Roda o grafo. Vamos ignorar as saídas "estranhas" dos eventos
            async for event in graph.astream(estado_input, config):
                logger.debug(f"⚙️ Evento do Grafo disparado: {list(event.keys())}")
            
            # 2. A MÁGICA: Buscar a verdade absoluta na Memória do LangGraph
            estado_wrapper = await graph.aget_state(config)
            novo_estado = estado_wrapper.values
            
            if not novo_estado:
                logger.error("❌ A Memória do LangGraph retornou vazia.")
                return "⚠️ Erro: O Grafo rodou, mas o MemorySaver falhou em guardar o estado."

            # 3. Observability
            estado_limpo = {k: v for k, v in novo_estado.items() if k != 'messages'}
            estado_json = json.dumps(estado_limpo, indent=2, ensure_ascii=False)
            
            logger.info(f"\n{'='*20} 📊 ESTADO RECUPERADO DA MEMÓRIA {'='*20}\n{estado_json}\n{'='*55}")

            # 4. Extração da Resposta
            if novo_estado.get("pending_confirmation"):
                return novo_estado.get("final_response", "⚠️ Confirmação pendente.")
                
            resposta_final = novo_estado.get("final_response")
            
            if resposta_final:
                return resposta_final
            
            # Se ainda assim estiver vazio, mostra a memória verdadeira!
            return (
                f"⚠️ **DEBUG MODE**\n"
                f"O fluxo chegou ao fim, mas a variável `final_response` está vazia.\n\n"
                f"*Estado salvo na Memória (Checkpointer):*\n"
                f"{estado_json}"
            )
            
        except Exception as e:
            logger.exception("❌ Erro fatal na invocação do Grafo.")
            return f"❌ Falha interna do motor: `{str(e)}`"