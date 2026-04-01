"""
rag/query_transform.py — Transformação de Queries com Contexto Factual e Histórico
========================================================================
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pydantic import BaseModel
from langchain_core.messages import BaseMessage

# Imports de Domínio e Aplicação (LIMPOS)
from src.memory.long_term_memory import Fato, fatos_como_string
from src.domain.ports.llm_Provider import ILLMProvider
from src.application.graph.prompts import PROMPT_QUERY_REWRITE # Certifique-se que o prompt está aqui!

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
_MIN_CHARS_PARA_TRANSFORM = 15
_TERMOS_JA_TECNICOS = frozenset({
    "matricula", "rematricula", "trancamento", "edital", "paes",
    "ac", "pcd", "br-ppi", "br-q", "br-dc", "ir-ppi",
    "prog", "proexae", "prppg", "prad", "ctic",
    "cecen", "cesb", "cesc", "ccsa",
    "2026.1", "2026.2", "calendario",
    "cronograma", "inscricao", "documentos",
})
_MAX_QUERY_CHARS = 200

# ─────────────────────────────────────────────────────────────────────────────
# Schemas (Structured Output)
# ─────────────────────────────────────────────────────────────────────────────
class QueryRewriteSchema(BaseModel):
    query_reescrita: str
    palavras_chave: list[str]

class SubQuerySchema(BaseModel):
    query_principal: str
    sub_queries: list[str]
    palavras_chave: list[str]

@dataclass
class QueryTransformada:
    query_original:   str
    query_principal:  str
    sub_queries:      list[str] = field(default_factory=list)
    palavras_chave:   list[str] = field(default_factory=list)
    foi_transformada: bool = False
    motivo:           str = ""

    @property
    def todas_queries(self) -> list[str]:
        queries = [self.query_principal]
        queries.extend(self.sub_queries)
        return queries

# ─────────────────────────────────────────────────────────────────────────────
# API Principal (100% Assíncrona e Injetada)
# ─────────────────────────────────────────────────────────────────────────────
async def transformar_query_async(
    pergunta: str,
    llm_provider: ILLMProvider, 
    historico_chat: list[BaseMessage] | None = None,
    fatos_usuario: list[Fato] | None = None,
    usar_sub_queries: bool = False,
) -> QueryTransformada:
    """
    Transforma a pergunta usando Memória de Curto Prazo (histórico) 
    e Longo Prazo (fatos) injetando o Provider (Clean Architecture).
    """
    pergunta_limpa = pergunta.strip()

    if not pergunta_limpa:
        return QueryTransformada(query_original=pergunta, query_principal=pergunta, motivo="vazia")

    # 1. Já é técnica?
    if not _precisa_transformar(pergunta_limpa, fatos_usuario):
        logger.info("⚡ Query já técnica, sem transform: '%s'", pergunta_limpa)
        return QueryTransformada(
            query_original=pergunta_limpa, query_principal=pergunta_limpa, motivo="query_tecnica"
        )

    # 2. Monta Contexto
    fatos_str = fatos_como_string(fatos_usuario) if fatos_usuario else "(sem histórico de longo prazo)"
    historico_str = "\n".join([f"{m.type.upper()}: {m.content}" for m in historico_chat[-4:]]) if historico_chat else ""

    # 3. Roteamento Inteligente
    if usar_sub_queries and _e_pergunta_complexa(pergunta_limpa):
        return await _transformar_com_sub_queries_async(pergunta_limpa, fatos_str, historico_str, llm_provider)
    else:
        return await _transformar_query_simples_async(pergunta_limpa, fatos_str, historico_str, llm_provider)

# ─────────────────────────────────────────────────────────────────────────────
# Transformações Internas Assíncronas
# ─────────────────────────────────────────────────────────────────────────────
async def _transformar_query_simples_async(pergunta: str, fatos_str: str, historico_str: str, llm: ILLMProvider) -> QueryTransformada:
    
    prompt = PROMPT_QUERY_REWRITE.format(
        fatos=fatos_str,
        pergunta=pergunta,
    )
    
    # Injeta a memória de curto prazo (para ele saber quem são "eles", "ele", etc)
    if historico_str:
        prompt = f"HISTÓRICO RECENTE DA CONVERSA:\n{historico_str}\n\n" + prompt

    # Usamos o método da Interface e passamos a classe Pydantic
    resultado = await llm.gerar_resposta_estruturada_async(
        prompt=prompt,
        response_schema=QueryRewriteSchema,
        temperatura=0.0
    )

    if not resultado:
        return QueryTransformada(query_original=pergunta, query_principal=pergunta, motivo="llm_falhou")

    # Como o provider retorna um objeto Pydantic validado:
    query_reescrita = resultado.query_reescrita.strip()
    
    if len(query_reescrita) < len(pergunta) * 0.5:
        query_reescrita = pergunta

    logger.info("🔄 Transformou: '%s' → '%s'", pergunta, query_reescrita[:60])

    return QueryTransformada(
        query_original=pergunta, query_principal=query_reescrita,
        palavras_chave=resultado.palavras_chave, foi_transformada=True, motivo="gemini_rewrite"
    )

async def _transformar_com_sub_queries_async(pergunta: str, fatos_str: str, historico_str: str, llm: ILLMProvider) -> QueryTransformada:
    
    prompt = (
        f"Histórico:\n{historico_str}\n\n"
        f"Fatos:\n{fatos_str}\n\n"
        f"Pergunta Complexa: {pergunta}\n\n"
        "Decompõe em 2 a 3 sub-perguntas técnicas e diretas."
    )

    resultado = await llm.gerar_resposta_estruturada_async(
        prompt=prompt,
        response_schema=SubQuerySchema,
        temperatura=0.0
    )

    if not resultado or not resultado.sub_queries:
        return await _transformar_query_simples_async(pergunta, fatos_str, historico_str, llm)

    logger.info("🔄 Sub-queries geradas: %d", len(resultado.sub_queries))

    return QueryTransformada(
        query_original=pergunta, query_principal=resultado.query_principal,
        sub_queries=resultado.sub_queries[:3], palavras_chave=resultado.palavras_chave,
        foi_transformada=True, motivo="sub_query_decomposition"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Heurísticas e Step-Back (Sem uso de LLM)
# ─────────────────────────────────────────────────────────────────────────────
def transformar_para_step_back(pergunta: str) -> str:
    pergunta_sem_nomes = re.sub(r'\b(prof\.?|professora?|dr\.?|doutora?)\s+\w+', '', pergunta, flags=re.IGNORECASE)
    pergunta_sem_nomes = re.sub(r'^(qual é|qual o|qual a|onde fica|como é)\s+', '', pergunta_sem_nomes, flags=re.IGNORECASE)
    pergunta_sem_detalhes = re.sub(r'\b\d{4,}\b', '', pergunta_sem_nomes)
    resultado = pergunta_sem_detalhes.strip()
    return resultado if len(resultado) > 10 else pergunta

def _precisa_transformar(pergunta: str, fatos: list[Fato] | None) -> bool:
    if len(pergunta) < _MIN_CHARS_PARA_TRANSFORM: return False
    pergunta_lower = _normalizar(pergunta)
    termos_encontrados = sum(1 for t in _TERMOS_JA_TECNICOS if t in pergunta_lower)
    if termos_encontrados >= 2: return False
    if termos_encontrados >= 1 and not fatos: return False
    return True

def _e_pergunta_complexa(pergunta: str) -> bool:
    conectores = ["e", "também", "além disso", "e também", "e qual", "e quando", "e como"]
    pergunta_lower = pergunta.lower()
    if len(pergunta) > 80 and any(c in pergunta_lower for c in conectores): return True
    if pergunta.count("?") > 1: return True
    return False

def _normalizar(texto: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return s.lower()