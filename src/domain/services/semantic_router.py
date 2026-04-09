# src/domain/services/semantic_router.py
import logging
import struct
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class ResultadoRoteamento:
    route: str  # Vai direto para o state["route"] (ex: "rag", "greeting", "crud")
    score: float = 0.0
    confianca: str = "baixa"
    tool_name: Optional[str] = None

class SemanticRouterService:
    def __init__(self, redis_client, embeddings_provider):
        """
        Injeção de dependências:
        - redis_client: Cliente do Redis com RediSearch ativo.
        - embeddings_provider: Classe que gera os vetores (Gemini/Groq).
        """
        self.redis = redis_client
        self.embeddings = embeddings_provider
        self.idx_name = "idx:tools"  # O nome do seu índice no Redis
        
        # Thresholds
        self.threshold_alta = 0.82
        self.threshold_media = 0.65

        # Mapeamento do que está no Redis para as rotas do seu edges.py
        self.name_para_rota = {
            "intent_greeting": "greeting",
            "intent_crud": "crud",
            "intent_admin": "admin",
            # Se achar ferramentas específicas, manda pro RAG (agentic) resolver
            "consultar_calendario_academico": "rag",
            "consultar_edital_paes_2026": "rag",
        }

    def rotear(self, texto: str, is_admin: bool = False) -> ResultadoRoteamento:
        """Faz a busca vetorial no Redis e retorna a rota para o LangGraph."""
        
        # 1. Fallbacks rápidos baseados em regex simples (Opcional, mas seguro)
        texto_lower = texto.lower().strip()
        if texto_lower in ["oi", "olá", "ola", "bom dia", "boa tarde", "boa noite"]:
            return ResultadoRoteamento(route="greeting", score=1.0, confianca="alta")

        # 2. Roteamento Semântico no Redis
        try:
            vetor = self.embeddings.embed_query(texto)
            vetor_bytes = struct.pack(f"{len(vetor)}f", *vetor)
            
            from redis.commands.search.query import Query as RQuery
            q = (
                RQuery("*=>[KNN 1 @embedding $vec AS score]")
                .sort_by("score")
                .return_fields("name", "score")
                .dialect(2)
            )
            
            results = self.redis.ft(self.idx_name).search(q, query_params={"vec": vetor_bytes})
            
            if results.docs:
                doc = results.docs[0]
                tool_name = getattr(doc, "name", "")
                similarity = 1.0 - float(getattr(doc, "score", 1.0))
                
                logger.debug("🎯 Roteamento KNN: '%s' | sim=%.4f", tool_name, similarity)

                if similarity >= self.threshold_media:
                    route = self.name_para_rota.get(tool_name, "rag") # Fallback para rag se não mapeado
                    
                    # Proteção extra: se a intent for admin, mas o state diz que não é admin, manda pro rag
                    if route == "admin" and not is_admin:
                        route = "rag"

                    confianca = "alta" if similarity >= self.threshold_alta else "media"
                    return ResultadoRoteamento(route=route, score=similarity, confianca=confianca, tool_name=tool_name)

        except Exception as e:
            logger.warning("⚠️ Falha na busca semântica KNN: %s", e)

        # 3. Fallback absoluto se nada der certo
        return ResultadoRoteamento(route="rag", score=0.0, confianca="baixa")