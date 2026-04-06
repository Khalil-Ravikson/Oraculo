"""
src/rag/query/strategies.py
----------------------------
Estratégias concretas de transformação de query.

ESTRATÉGIAS IMPLEMENTADAS:
  1. PassthroughStrategy  — retorna a query sem modificação (baseline)
  2. KeywordEnrichStrategy — enriquece com keywords do domínio UEMA
  3. StepBackStrategy     — gera versão mais genérica (para fallback/CRAG)
  4. HyDEStrategy         — Hypothetical Document Embeddings (Gao et al., 2022)
  5. MultiQueryStrategy   — decompõe em sub-queries paralelas
  6. RAGFusionStrategy    — gera N variantes para fusão de rankings

QUANDO USAR CADA UMA:
  Pergunta curta, específica ("email PROG") → KeywordEnrich é suficiente
  Pergunta vaga ("me explica como me cadastrar") → StepBack → MultiQuery
  Pergunta complexa de compreensão ("como funciona o PAES") → HyDE
  Pergunta que pode ter múltiplos ângulos → RAGFusion

CUSTO DE TOKENS (estimativa por chamada):
  PassthroughStrategy  → 0 tokens
  KeywordEnrichStrategy → 0 tokens (regex local)
  StepBackStrategy     → ~60 tokens (mini-prompt)
  HyDEStrategy         → ~200 tokens (gera documento hipotético)
  MultiQueryStrategy   → ~120 tokens (decompõe)
  RAGFusionStrategy    → ~150 tokens (gera variantes)
"""
from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache

from .protocols import AbstractQueryStrategy, RawQuery, TransformedQuery

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de domínio UEMA (usadas por múltiplas estratégias)
# ─────────────────────────────────────────────────────────────────────────────

_TERMOS_TECNICOS = frozenset({
    "matricula", "rematricula", "trancamento", "edital", "paes",
    "ac", "pcd", "br-ppi", "br-q", "br-dc", "ir-ppi",
    "prog", "proexae", "prppg", "prad", "ctic",
    "cecen", "cesb", "cesc", "ccsa", "ceea",
    "2026.1", "2026.2", "calendario", "cronograma",
    "inscricao", "documentos", "sigaa", "glpi",
})

_SINONIMOS_UEMA: dict[str, list[str]] = {
    "matricula":    ["rematricula", "inscricao", "periodo letivo"],
    "trancamento":  ["cancelamento disciplina", "trancar"],
    "cotas":        ["br-ppi", "br-q", "pcd", "ir-ppi", "reserva de vagas"],
    "email":        ["e-mail", "contato", "endereco eletronico"],
    "suporte":      ["ctic", "helpdesk", "ti", "chamado"],
    "calendario":   ["datas", "prazos", "semestre", "letivo"],
    "edital":       ["paes", "vestibular", "processo seletivo"],
}


def _normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return s.lower().strip()


def _tem_termos_tecnicos(query: str, min_termos: int = 2) -> bool:
    norm = _normalizar(query)
    palavras = set(re.split(r"\W+", norm))
    return sum(1 for t in _TERMOS_TECNICOS if t in palavras or any(t in p for p in palavras)) >= min_termos


# ─────────────────────────────────────────────────────────────────────────────
# 1. Passthrough
# ─────────────────────────────────────────────────────────────────────────────

class PassthroughStrategy(AbstractQueryStrategy):
    """Retorna query sem modificação. Usada como baseline e fallback seguro."""

    @property
    def name(self) -> str:
        return "passthrough"

    def transform(self, query: RawQuery) -> TransformedQuery:
        return TransformedQuery(
            original=query.text,
            primary=query.text,
            strategy_used="passthrough",
            was_transformed=False,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. KeywordEnrich
# ─────────────────────────────────────────────────────────────────────────────

class KeywordEnrichStrategy(AbstractQueryStrategy):
    """
    Enriquece a query com termos técnicos e sinônimos do domínio UEMA.
    Operação local (0 tokens, 0ms de rede).

    Funciona melhor para queries que já mencionam algum conceito UEMA
    mas de forma informal (ex: "quando posso cancelar matéria" → adiciona
    "trancamento matrícula prazo semestre").
    """

    @property
    def name(self) -> str:
        return "keyword_enrich"

    def should_apply(self, query: RawQuery) -> bool:
        norm = _normalizar(query.text)
        # Só enriquece se a query não é já técnica suficiente
        return not _tem_termos_tecnicos(norm, min_termos=3)

    def transform(self, query: RawQuery) -> TransformedQuery:
        norm = _normalizar(query.text)
        termos_extra: list[str] = []

        for termo, sinonimos in _SINONIMOS_UEMA.items():
            if termo in norm:
                termos_extra.extend(sinonimos[:2])  # máx 2 sinônimos por termo

        # Injeta fatos do utilizador na query
        fatos_resumo = ""
        if query.fatos_usuario:
            fatos_resumo = " ".join(query.fatos_usuario[:2])  # top-2 fatos

        partes = [query.text]
        if termos_extra:
            partes.append(" ".join(set(termos_extra)))
        if fatos_resumo:
            partes.append(fatos_resumo)

        enriched = " ".join(partes)[:300]  # trunca para não explodir o índice BM25

        return TransformedQuery(
            original=query.text,
            primary=enriched,
            keywords=list(set(termos_extra)),
            strategy_used="keyword_enrich",
            was_transformed=enriched != query.text,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. StepBack
# ─────────────────────────────────────────────────────────────────────────────

class StepBackStrategy(AbstractQueryStrategy):
    """
    Gera uma versão mais genérica da query (Step-Back Prompting, Zheng et al. 2023).
    Usado como:
      a) Fallback no CRAG quando o retrieval primário tem score baixo
      b) Geração de query de contexto amplo para perguntas muito específicas

    Implementação HEURÍSTICA (sem LLM) para custo zero:
      - Remove nomes próprios e datas específicas
      - Mantém conceitos-chave do domínio
      - Generaliza "Engenharia Civil noturno" → "curso período"
    """

    @property
    def name(self) -> str:
        return "step_back"

    def should_apply(self, query: RawQuery) -> bool:
        # Aplica apenas para queries longas (>5 palavras) ou muito específicas
        return len(query.text.split()) >= 5 or bool(re.search(r"\d{2}/\d{2}/\d{4}", query.text))

    def transform(self, query: RawQuery) -> TransformedQuery:
        texto = query.text

        # Remove datas específicas
        texto = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", texto)
        texto = re.sub(r"\b20\d{2}[\./]\d{1,2}\b", "", texto)

        # Generaliza siglas de cotas
        texto = re.sub(r"\b(br-ppi|br-q|br-dc|ir-ppi|cfo-pp|pcd)\b", "cota", texto, flags=re.I)

        # Remove nomes próprios prováveis (sequência >3 chars com inicial maiúscula)
        texto = re.sub(r"\b([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]{3,})\b", "", texto)

        # Limpa espaços múltiplos
        step_back = " ".join(texto.split())

        # Fallback: se ficou muito curta, usa as primeiras 3 palavras
        if len(step_back) < 10:
            step_back = " ".join(query.text.split()[:3])

        return TransformedQuery(
            original=query.text,
            primary=query.text,   # primary continua sendo a original
            step_back=step_back,  # step_back é o fallback generalizante
            strategy_used="step_back",
            was_transformed=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. HyDE (Hypothetical Document Embeddings)
# ─────────────────────────────────────────────────────────────────────────────

class HyDEStrategy(AbstractQueryStrategy):
    """
    Hypothetical Document Embeddings (Gao et al., 2022).
    
    IDEIA:
      Em vez de buscar diretamente pela query do utilizador, geramos um
      "documento hipotético" que RESPONDERIA a query, depois buscamos
      chunks similares a esse documento hipotético.

      Por que isso ajuda?
        A query "quando é a matrícula?" está semanticamente distante
        do chunk "EVENTO: Matrícula de veteranos | DATA: 03/02/2026".
        Mas um documento hipotético "A matrícula de veteranos ocorre em
        fevereiro de 2026, especificamente entre os dias 3 e 7." está
        muito próximo do chunk original.

    CUSTO: ~200 tokens por chamada (vale a pena para queries complexas).
    Só aplica quando a query é pergunta aberta e rota é específica (não GERAL).
    """

    def __init__(self, llm_provider=None):
        self._llm = llm_provider

    @property
    def name(self) -> str:
        return "hyde"

    def should_apply(self, query: RawQuery) -> bool:
        if self._llm is None:
            return False
        # Aplica para perguntas abertas (terminam em "?") não muito técnicas
        text = query.text.strip()
        is_question = text.endswith("?") or text.lower().startswith(
            ("quando", "o que", "qual", "como", "onde", "quem", "por que", "quais")
        )
        return is_question and not _tem_termos_tecnicos(text, min_termos=3)

    def transform(self, query: RawQuery) -> TransformedQuery:
        if self._llm is None:
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="hyde_skipped", was_transformed=False,
            )

        fatos_ctx = "\n".join(f"- {f}" for f in query.fatos_usuario[:3]) if query.fatos_usuario else ""
        prompt = _PROMPT_HYDE.format(pergunta=query.text, fatos=fatos_ctx or "Sem contexto do aluno.")

        try:
            resposta = self._llm.gerar_resposta_sincrono(prompt, temperatura=0.3, max_tokens=200)
            doc_hipotetico = (resposta.conteudo or "").strip()
            if not doc_hipotetico or len(doc_hipotetico) < 30:
                raise ValueError("Documento hipotético muito curto")

            logger.info("🧠 HyDE gerado (%.60s...)", doc_hipotetico)
            return TransformedQuery(
                original=query.text,
                primary=doc_hipotetico,   # usa o doc hipotético como query principal
                variants=[query.text],    # mantém original como variante
                hypothetical_doc=doc_hipotetico,
                strategy_used="hyde",
                was_transformed=True,
            )
        except Exception as e:
            logger.warning("⚠️  HyDE falhou, usando passthrough: %s", e)
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="hyde_failed", was_transformed=False,
            )


_PROMPT_HYDE = """Você é um assistente acadêmico da UEMA (Universidade Estadual do Maranhão).
Escreva um parágrafo FACTUAL e CONCISO que responderia diretamente à seguinte pergunta de um aluno.
Use linguagem técnica institucional. Não invente datas — use aproximações genéricas.

Contexto do aluno:
{fatos}

Pergunta: {pergunta}

Responda com 2-3 frases como se fosse um trecho do manual ou calendário da UEMA:"""


# ─────────────────────────────────────────────────────────────────────────────
# 5. MultiQuery
# ─────────────────────────────────────────────────────────────────────────────

class MultiQueryStrategy(AbstractQueryStrategy):
    """
    Decompõe queries complexas em sub-queries independentes.

    QUANDO USAR:
      Perguntas com múltiplos aspectos: "quais documentos preciso e até
      quando devo entregar para me inscrever no PAES com cota PcD?"
      → Sub-query 1: "documentos necessários inscrição PAES 2026"
      → Sub-query 2: "prazo inscrição PAES 2026 cronograma"
      → Sub-query 3: "categoria PcD deficiência vagas PAES"

    Os resultados de cada sub-query são buscados separadamente e fundidos
    via RRF no HybridRetriever.
    """

    def __init__(self, llm_provider=None):
        self._llm = llm_provider

    @property
    def name(self) -> str:
        return "multi_query"

    def should_apply(self, query: RawQuery) -> bool:
        if self._llm is None:
            return False
        text = query.text
        # Heurística: query longa com conectivos de múltiplas intenções
        conectivos = ["e", "também", "além disso", "e qual", "e quando", "e como", "bem como"]
        tem_conectivo = any(c in text.lower() for c in conectivos)
        return len(text) > 60 and tem_conectivo

    def transform(self, query: RawQuery) -> TransformedQuery:
        if self._llm is None:
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="multi_query_skipped", was_transformed=False,
            )

        fatos_ctx = "\n".join(f"- {f}" for f in query.fatos_usuario[:3]) if query.fatos_usuario else ""
        prompt = _PROMPT_MULTI.format(pergunta=query.text, fatos=fatos_ctx or "Sem contexto.")

        try:
            resposta = self._llm.gerar_resposta_sincrono(prompt, temperatura=0.1, max_tokens=300)
            conteudo = (resposta.conteudo or "").strip()

            # Parse: cada linha não vazia é uma sub-query
            sub_queries = [
                linha.strip().lstrip("-•123456789.) ")
                for linha in conteudo.splitlines()
                if len(linha.strip()) > 10
            ][:4]  # máximo 4 sub-queries

            if not sub_queries:
                raise ValueError("Nenhuma sub-query extraída")

            logger.info("🔀 Multi-query: %d sub-queries para '%s'", len(sub_queries), query.text[:50])
            return TransformedQuery(
                original=query.text,
                primary=sub_queries[0],
                variants=sub_queries[1:],
                strategy_used="multi_query",
                was_transformed=True,
            )
        except Exception as e:
            logger.warning("⚠️  Multi-query falhou: %s", e)
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="multi_query_failed", was_transformed=False,
            )


_PROMPT_MULTI = """Decomponha a pergunta abaixo em sub-perguntas simples e independentes para busca em banco de documentos da UEMA.
Gere no máximo 4 sub-perguntas técnicas, uma por linha, sem numeração.
Cada sub-pergunta deve ser autocontida e focada em UM aspecto da pergunta original.

Contexto do aluno: {fatos}
Pergunta original: {pergunta}

Sub-perguntas (uma por linha):"""


# ─────────────────────────────────────────────────────────────────────────────
# 6. RAGFusion
# ─────────────────────────────────────────────────────────────────────────────

class RAGFusionStrategy(AbstractQueryStrategy):
    """
    RAG Fusion: gera N variantes da query com perspectivas diferentes,
    busca cada uma e funde via RRF para melhor cobertura semântica.

    DIFERENÇA vs MultiQuery:
      MultiQuery → decompõe UMA pergunta complexa em sub-aspectos
      RAGFusion  → gera N PERSPECTIVAS diferentes da MESMA pergunta simples

    Ex: "matrícula veteranos" →
      Variante 1: "período rematrícula alunos veteranos 2026.1"
      Variante 2: "prazo renovação matrícula semestral UEMA"
      Variante 3: "calendário acadêmico matrícula fevereiro 2026"
    """

    def __init__(self, llm_provider=None, n_variantes: int = 3):
        self._llm = llm_provider
        self._n = n_variantes

    @property
    def name(self) -> str:
        return "rag_fusion"

    def should_apply(self, query: RawQuery) -> bool:
        if self._llm is None:
            return False
        # Aplica para queries de tamanho médio sem termos muito específicos
        return 20 <= len(query.text) <= 120

    def transform(self, query: RawQuery) -> TransformedQuery:
        if self._llm is None:
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="rag_fusion_skipped", was_transformed=False,
            )

        prompt = _PROMPT_RAG_FUSION.format(
            pergunta=query.text,
            n=self._n,
            fatos="\n".join(f"- {f}" for f in query.fatos_usuario[:2]) or "Sem contexto.",
        )

        try:
            resposta = self._llm.gerar_resposta_sincrono(prompt, temperatura=0.2, max_tokens=250)
            conteudo = (resposta.conteudo or "").strip()

            variantes = [
                linha.strip().lstrip("-•123456789.) ")
                for linha in conteudo.splitlines()
                if len(linha.strip()) > 8
            ][:self._n]

            if not variantes:
                raise ValueError("Sem variantes geradas")

            return TransformedQuery(
                original=query.text,
                primary=query.text,  # primária é sempre a original
                variants=variantes,
                strategy_used="rag_fusion",
                was_transformed=True,
            )
        except Exception as e:
            logger.warning("⚠️  RAGFusion falhou: %s", e)
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="rag_fusion_failed", was_transformed=False,
            )


_PROMPT_RAG_FUSION = """Gere {n} reformulações alternativas da pergunta abaixo para melhorar a busca em documentos da UEMA.
Cada reformulação deve usar palavras diferentes mas cobrir o mesmo tema.
Escreva uma por linha, sem numeração.

Contexto do aluno: {fatos}
Pergunta: {pergunta}

Reformulações:"""