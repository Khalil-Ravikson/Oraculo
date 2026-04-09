"""
src/domain/services/oraculo_router.py
==========================================================================
O Roteador Mestre em 3 Camadas:
1. Semantic Cache: Tenta achar a resposta exata já processada antes (Custo: $0)
2. KNN Vector Router: Busca a intenção via Embeddings no Redis (Custo: $0.0001)
3. Pydantic Router (LLM): Se o KNN não tiver 85%+ de certeza, usa o Gemini para raciocinar (Custo: $0.01)
"""
import logging
import struct
import time
from dataclasses import dataclass
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Schema exigido do Gemini na Tentativa 3
class RoutingDecision(BaseModel):
    decisao: str = Field(description="A rota: CALENDARIO, EDITAL, CONTATOS, WIKI, CRUD, GREETING, ou GERAL")
    confianca: float = Field(description="Nível de certeza de 0.0 a 1.0")
    motivo: str = Field(description="Justificativa breve")
    intencao_crud: bool = Field(description="True se o usuário quer alterar seus próprios dados")

@dataclass
class RouterResult:
    route: str           # Nome do nó no LangGraph (ex: "rag_node", "crud_node")
    confianca: float
    metodo: str          # "semantic_cache" | "vector_knn" | "pydantic_llm" | "fallback_regex"
    motivo: str
    cached_response: str | None = None # Se tiver, o LangGraph só devolve isso

class OraculoRouterService:
    def __init__(self, redis_client, embeddings):
        self.redis = redis_client
        self.embeddings = embeddings
        self.idx_tools = "idx:tools"
        self.idx_cache = "idx:semantic_cache"
        
        # O Threshold Mágico: Só aciona LLM se a similaridade for menor que 85%
        self.threshold_knn_bypass = 0.85 
        
        # Mapeamento Padrão
        self.mapa_rotas = {
            "CALENDARIO": "rag_node",
            "EDITAL": "rag_node",
            "CONTATOS": "rag_node",
            "WIKI": "rag_node",
            "CRUD": "crud_node",
            "GREETING": "greeting_node",
            "GERAL": "rag_node",
            "intent_greeting": "greeting_node",
            "intent_crud": "crud_node",
            "intent_admin": "admin_command_node"
        }

    def rotear(self, mensagem: str, contexto: dict) -> RouterResult:
        """Pipeline em 3 Etapas para economizar tokens e garantir precisão."""
        
        # =================================================================
        # TENTATIVA 1: Semantic Cache (Já respondemos isso antes?)
        # =================================================================
        # Aqui você faria a busca no índice de respostas prontas.
        # Se achou: return RouterResult(route="respond_node", cached_response="...", metodo="semantic_cache", ...)

        vetor = None
        try:
            vetor = self.embeddings.embed_query(mensagem)
        except Exception as e:
            logger.warning("⚠️ Falha ao gerar embedding: %s", e)

        # =================================================================
        # TENTATIVA 2: KNN Vector Router (Rápido e Barato)
        # =================================================================
        if vetor:
            try:
                vetor_bytes = struct.pack(f"{len(vetor)}f", *vetor)
                from redis.commands.search.query import Query as RQuery
                q = RQuery("*=>[KNN 1 @embedding $vec AS score]").sort_by("score").return_fields("name", "score").dialect(2)
                
                results = self.redis.ft(self.idx_tools).search(q, query_params={"vec": vetor_bytes})
                
                if results.docs:
                    doc = results.docs[0]
                    nome_intent = getattr(doc, "name", "")
                    similaridade = 1.0 - float(getattr(doc, "score", 1.0))
                    
                    # Se tivermos ALTA certeza (> 0.85), usamos o vetor e ignoramos o LLM!
                    if similaridade >= self.threshold_knn_bypass:
                        route = self.mapa_rotas.get(nome_intent, "rag_node")
                        return RouterResult(
                            route=route, confianca=similaridade, metodo="vector_knn", 
                            motivo=f"Similaridade alta com {nome_intent}"
                        )
                    else:
                        logger.debug("🤔 KNN achou %s com %.2f de certeza. Ambiguidade detectada. Chamando LLM...", nome_intent, similaridade)
            except Exception as e:
                logger.warning("⚠️ KNN Vector Search falhou: %s", e)

        # =================================================================
        # TENTATIVA 3: Pydantic Router (O Detetive Especialista)
        # =================================================================
        try:
            import google.genai as genai
            from google.genai import types
            from src.infrastructure.settings import settings
            import json

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            
            # Prompt enxuto para gastar pouco
            sys_prompt = "Classifique a intenção do aluno (rotas: CALENDARIO, EDITAL, CONTATOS, WIKI, CRUD, GREETING, GERAL). Responda apenas o JSON."
            
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=f"Mensagem: '{mensagem}'",
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=RoutingDecision,
                )
            )
            
            data = json.loads(response.text or "{}")
            decision = RoutingDecision(**data)
            
            route = "crud_node" if decision.intencao_crud else self.mapa_rotas.get(decision.decisao, "rag_node")
            
            return RouterResult(
                route=route, confianca=decision.confianca, metodo="pydantic_llm", motivo=decision.motivo
            )

        except Exception as e:
            logger.error("❌ Pydantic Router falhou: %s", e)
            
        # =================================================================
        # FALLBACK FINAL
        # =================================================================
        return RouterResult(route="rag_node", confianca=0.0, metodo="fallback", motivo="Tudo falhou")