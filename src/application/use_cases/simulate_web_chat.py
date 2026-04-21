import logging
from src.application.chain.oracle_chain import get_oracle_chain

logger = logging.getLogger(__name__)

class SimulateWebChatUseCase:
    """
    Caso de uso para simular o chat do WhatsApp através do painel Web Admin.
    Atualizado para a v5: Usa LangChain Runnables (OracleChain) em vez de LangGraph.
    """
    
    async def executar(self, session_id: str, mensagem: str, admin_name: str) -> str:
        logger.info(f"🤖 [SIMULADOR] Processando mensagem de {admin_name}: '{mensagem}'")
        
        try:
            # Pega a nossa nova cadeia linear
            chain = get_oracle_chain()
            
            # Monta o contexto do utilizador admin
            user_context = {
                "nome": admin_name,
                "role": "admin",
                "is_admin": True
            }
            
            # Executa a cadeia de forma assíncrona
            result = await chain.invoke(
                message=mensagem,
                session_id=session_id,
                user_context=user_context
            )
            
            # Tratamento de erros limpo
            if result.error:
                logger.error(f"❌ [SIMULADOR] Erro na chain: {result.error}")
                return f"Tive um erro interno ao processar: {result.error}"
                
            if not result.answer:
                logger.warning(f"⚠️ [SIMULADOR] A chain rodou, mas a resposta final veio vazia.")
                return "⚠️ A IA não gerou nenhuma resposta (retorno vazio)."
                
            logger.info(f"✅ [SIMULADOR] Resposta gerada com sucesso! Rota: {result.route}")
            return result.answer
            
        except Exception as e:
            logger.exception("❌ [SIMULADOR] Erro fatal no simulador web")
            return f"⚠️ Erro grave ao simular chat: {str(e)}"