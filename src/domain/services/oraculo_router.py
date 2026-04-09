# src/domain/services/oraculo_router.py
import logging

logger = logging.getLogger(__name__)

class OraculoRouterService:
    def __init__(self, semantic_router, pydantic_router):
        """Recebe os dois roteadores como peças de montar."""
        self.semantic = semantic_router
        self.pydantic = pydantic_router
        
        # A SUA REGRA DE NEGÓCIO: O Threshold (Ponto de Corte)
        self.limite_confianca = 0.85

    def rotear(self, mensagem: str, contexto: dict, is_admin: bool = False) -> dict:
        logger.info("🚦 OráculoRouter: Iniciando Roteamento em Cascata...")

        # =================================================================
        # TENTATIVA 1: O Rápido (Semantic Router via Redis)
        # =================================================================
        try:
            res_semantico = self.semantic.rotear(mensagem, is_admin=is_admin)
            
            # AVALIA O SCORE: Se for maior que 0.85, confia e para por aqui!
            if res_semantico.score >= self.limite_confianca:
                logger.info("⚡ Sucesso na Tentativa 1! Rota Semântica: %s (Score: %.2f)", 
                            res_semantico.route, res_semantico.score)
                return {
                    "route": res_semantico.route,
                    "skip_cache": False
                }
            else:
                logger.debug("🤔 Tentativa 1 Falhou (Score %.2f < %.2f). Repassando para a IA...", 
                             res_semantico.score, self.limite_confianca)
                
        except Exception as e:
            logger.warning("⚠️ Erro na Tentativa 1 (Semântico): %s", e)


        # =================================================================
        # TENTATIVA 2: O Inteligente (Pydantic Router via Gemini)
        # =================================================================
        try:
            res_pydantic = self.pydantic.rotear(mensagem, contexto_usuario=contexto)
            
            logger.info("🧠 Sucesso na Tentativa 2! A IA decidiu: %s (Motivo: %s)", 
                        res_pydantic.decisao, res_pydantic.motivo)
            
            # Converte a decisão da IA para os NÓS do LangGraph
            mapa_nós = {
                "CALENDARIO": "rag_node",
                "EDITAL": "rag_node",
                "CONTATOS": "rag_node",
                "WIKI": "rag_node",
                "CRUD": "crud_node",
                "GREETING": "greeting_node",
                "GERAL": "rag_node"
            }
            
            return {
                "route": mapa_nós.get(res_pydantic.decisao, "rag_node"),
                "skip_cache": res_pydantic.skip_cache
            }
            
        except Exception as e:
            logger.error("❌ Erro na Tentativa 2 (Pydantic): %s", e)


        # =================================================================
        # TENTATIVA 3: O Salva-Vidas (Fallback de Queda)
        # =================================================================
        logger.error("🚨 Todos os roteadores falharam (Redis e Gemini fora do ar?). Padrão RAG.")
        return {
            "route": "rag_node",
            "skip_cache": True
        }