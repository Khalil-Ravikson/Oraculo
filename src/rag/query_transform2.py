"""
rag/query_transform.py — Transformação de Queries com Contexto Factual
========================================================================
(Documentação mantida...)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pydantic import BaseModel

from src.memory.long_term_memory import Fato, fatos_como_string
from src.domain.ports.llm_provider import ILLMProvider
from src.agent.prompts import PROMPT_QUERY_REWRITE # Certifique-se de importar o prompt de onde ele vive!

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schemas (Modelos de Dados do Caso de Uso)
# ─────────────────────────────────────────────────────────────────────────────

class QueryRewriteSchema(BaseModel):
    query_reescrita: str
    palavras_chave: list[str]

class SubQuerySchema(BaseModel):
    query_principal: str
    sub_queries: list[str]
    palavras_chave: list[str]

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

_MIN_CHARS_PARA_TRANSFORM = 15
_MAX_QUERY_CHARS = 200
_TERMOS_JA_TECNICOS = frozenset({
    "matricula", "rematricula", "trancamento", "edital", "paes",
    "ac", "pcd", "br-ppi", "br-q", "br-dc", "ir-ppi",
    "prog", "proexae", "prppg", "prad", "ctic",
    "cecen", "cesb", "cesc", "ccsa",
    "2026.1", "2026.2", "calendario",
    "cronograma", "inscricao", "documentos",
})

# ─────────────────────────────────────────────────────────────────────────────
# Tipos de Retorno
# ─────────────────────────────────────────────────────────────────────────────

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

    @property
    def query_para_log(self) -> str:
        arrow = " → " if self.foi_transformada else " (sem transform)"
        return f"'{self.query_original[:40]}'{arrow}'{self.query_principal[:60]}'"


# ─────────────────────────────────────────────────────────────────────────────
# API principal (AGORA É ASSÍNCRONA E RECEBE A INTERFACE LLM)
# ─────────────────────────────────────────────────────────────────────────────

async def transformar_query_async(
    pergunta: str,
    llm_provider: ILLMProvider,  # INJEÇÃO DE DEPENDÊNCIA AQUI!
    fatos_usuario: list[Fato] | None = None,
    usar_sub_queries: bool = False,
) -> QueryTransformada:
    """
    Transforma a pergunta do utilizador numa query otimizada para busca.
    """
    pergunta_limpa = pergunta.strip()

    if not pergunta_limpa:
        return QueryTransformada(query_original=pergunta, query_principal=pergunta, motivo="pergunta vazia")

    # 1. Verifica se já é técnica
    if not _precisa_transformar(pergunta_limpa, fatos_usuario):
        logger.debug("⚡ Query já técnica, sem transform: '%.50s'", pergunta_limpa)
        return QueryTransformada(
            query_original=pergunta_limpa, query_principal=pergunta_limpa, 
            foi_transformada=False, motivo="query já técnica"
        )

    # 2. Monta texto dos fatos
    fatos_str = fatos_como_string(fatos_usuario) if fatos_usuario else "(sem histórico do aluno)"

    # 3. Passa o Provider para as funções que farão o trabalho pesado
    if usar_sub_queries and _e_pergunta_complexa(pergunta_limpa):
        return await _transformar_com_sub_queries_async(pergunta_limpa, fatos_str, llm_provider)
    else:
        return await _transformar_query_simples_async(pergunta_limpa, fatos_str, llm_provider)


# ─────────────────────────────────────────────────────────────────────────────
# Transformações internas usando o Provider Genérico
# ─────────────────────────────────────────────────────────────────────────────

async def _transformar_query_simples_async(pergunta: str, fatos_str: str, llm: ILLMProvider) -> QueryTransformada:
    prompt = PROMPT_QUERY_REWRITE.format(
        fatos=fatos_str or "Nenhum fato disponível.",
        pergunta=pergunta,
    )

    # Chamamos o LLM passando o SCHEMA
    resultado = await llm.gerar_resposta_estruturada_async(
        prompt=prompt,
        response_schema=QueryRewriteSchema,
        temperatura=0.1,
    )

    # Fallback caso o LLM falhe
    if not resultado:
        logger.warning("⚠️  Query transform falhou, usando original: '%.50s'", pergunta)
        return QueryTransformada(
            query_original=pergunta, query_principal=pergunta, 
            foi_transformada=False, motivo="llm_falhou"
        )

    # CUIDADO AQUI: resultado agora é um objeto Pydantic (QueryRewriteSchema), não um dicionário!
    query_reescrita = resultado.query_reescrita.strip()
    palavras_chave  = resultado.palavras_chave

    # Guarda de segurança (Truncamento do LLM)
    if not query_reescrita or len(query_reescrita) < len(pergunta) * 0.5:
        logger.debug("⚠️  Query reescrita muito curta, usando original")
        query_reescrita = pergunta

    query_reescrita = query_reescrita[:_MAX_QUERY_CHARS]
    logger.info("🔄 Query rewrite: '%.40s' → '%.60s'", pergunta, query_reescrita)

    return QueryTransformada(
        query_original=pergunta, query_principal=query_reescrita,
        palavras_chave=palavras_chave, foi_transformada=True, motivo="llm_rewrite"
    )


async def _transformar_com_sub_queries_async(pergunta: str, fatos_str: str, llm: ILLMProvider) -> QueryTransformada:
    _PROMPT_SUB = (
        "Decompõe a pergunta complexa abaixo em sub-perguntas simples.\n\n"
        "Fatos do aluno:\n<fatos>{fatos}</fatos>\n\n"
        "Pergunta: <pergunta>{pergunta}</pergunta>\n\n"
        "Cria 2-3 sub-perguntas técnicas que juntas respondem à pergunta original.\n"
        "query_principal deve ser uma versão abrangente da pergunta original."
    )

    prompt = _PROMPT_SUB.format(fatos=fatos_str or "Nenhum fato.", pergunta=pergunta)

    resultado = await llm.gerar_resposta_estruturada_async(
        prompt=prompt,
        response_schema=SubQuerySchema,
        temperatura=0.1,
    )

    if not resultado:
        logger.debug("⚠️  Sub-query decomposition falhou → fallback")
        return await _transformar_query_simples_async(pergunta, fatos_str, llm)

    # Novamente: Acessando como Objeto!
    query_principal = resultado.query_principal.strip()[:_MAX_QUERY_CHARS]
    sub_queries = [
        q.strip()[:_MAX_QUERY_CHARS]
        for q in resultado.sub_queries
        if q.strip() and len(q.strip()) > 5
    ]
    palavras_chave = resultado.palavras_chave

    if not sub_queries:
        return await _transformar_query_simples_async(pergunta, fatos_str, llm)

    logger.info("🔄 Sub-queries: '%.40s' → principal='%.50s' + %d subs", pergunta, query_principal, len(sub_queries))

    return QueryTransformada(
        query_original=pergunta, query_principal=query_principal,
        sub_queries=sub_queries[:3], palavras_chave=palavras_chave,
        foi_transformada=True, motivo="sub_query_decomposition",
    )

# ─────────────────────────────────────────────────────────────────────────────
# Step-Back e Heurísticas (Ficam Iguais pois não usam LLM!)
# ─────────────────────────────────────────────────────────────────────────────

def transformar_para_step_back(pergunta: str) -> str:
    # (Seu código original aqui, não mudou nada)
    pergunta_sem_nomes = re.sub(r'\b(prof\.?|professora?|dr\.?|doutora?)\s+\w+', '', pergunta, flags=re.IGNORECASE)
    pergunta_sem_nomes = re.sub(r'^(qual é|qual o|qual a|onde fica|como é)\s+', '', pergunta_sem_nomes, flags=re.IGNORECASE)
    pergunta_sem_detalhes = re.sub(r'\b\d{4,}\b', '', pergunta_sem_nomes)
    resultado = pergunta_sem_detalhes.strip()
    return resultado if len(resultado) > 10 else pergunta

def _precisa_transformar(pergunta: str, fatos: list[Fato] | None) -> bool:
    # (Seu código original aqui)
    if len(pergunta) < _MIN_CHARS_PARA_TRANSFORM: return False
    pergunta_lower = _normalizar(pergunta)
    termos_encontrados = sum(1 for t in _TERMOS_JA_TECNICOS if t in pergunta_lower)
    if termos_encontrados >= 2: return False
    if termos_encontrados >= 1 and not fatos: return False
    return True

def _e_pergunta_complexa(pergunta: str) -> bool:
    # (Seu código original aqui)
    conectores = ["e", "também", "além disso", "e também", "e qual", "e quando", "e como"]
    pergunta_lower = pergunta.lower()
    if len(pergunta) > 80 and any(c in pergunta_lower for c in conectores): return True
    if pergunta.count("?") > 1: return True
    return False

def _normalizar(texto: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return s.lower()