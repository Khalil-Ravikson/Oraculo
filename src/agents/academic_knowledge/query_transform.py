"""
src/agents/academic_knowledge/query_transform.py
===================================================
Ex `QueryTransformService` de `infrastructure/services/rag_search_service.py`
(Fase 4 do PLANO_REFATORACAO_SUPERVISOR.md, seção 2.4). Comportamento
idêntico, só relocado.

NÃO CONFUNDIR com `src/rag/query_transform.py` — esse é um módulo
pré-existente e não relacionado, parte de um pipeline de RAG mais antigo
(Clean Architecture com `ILLMProvider`/`RetrieveContextUseCase`) que não está
no caminho quente de produção (só é exercitado por
`application/use_cases/retrieve_context_use_case.py` e por um script manual
`tests/e2e/test_novo_oraculo.py`). Fora do escopo deste plano.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TransformedQuery:
    original: str
    primary: str
    variants: list[str] = field(default_factory=list)
    step_back: str = ""
    keywords: list[str] = field(default_factory=list)
    strategy_used: str = "passthrough"
    was_transformed: bool = False

    @property
    def all_queries(self) -> list[str]:
        queries = [self.primary]
        queries.extend(v for v in self.variants if v and v != self.primary)
        return queries


class QueryTransformService:
    """
    Transforma a query do usuário para melhorar o recall e precisão da busca RAG.

    ESTRATÉGIAS UTILIZADAS:
      1. ProperNounQueryStrategy: regex local que envolve nomes próprios em aspas
         para forçar exact matching via BM25/FTS no Redis.
      2. KeywordEnrich: enriquece com sinônimos do domínio UEMA e fatos do usuário.
      3. StepBackStrategy: gera uma query de generalização (fallback) removendo
         datas e especificações, permitindo busca ampla.
      4. Gemini Flash: reescrita contextual em caso de pronomes/queries vagas.
    """

    _SINONIMOS = {
        "matricula":   ["rematricula", "inscricao semestral"],
        "trancamento": ["cancelamento disciplina", "trancar materia"],
        "cotas":       ["br-ppi", "br-q", "pcd", "reserva de vagas"],
        "suporte":     ["ctic", "helpdesk", "chamado ti"],
        "calendario":  ["datas letivas", "prazo academico", "semestre"],
    }

    def transformar_local(
        self,
        query: str,
        fatos: list[str] | None = None,
    ) -> str:
        """Enriquece a query localmente usando sinônimos do domínio e fatos do usuário."""
        norm = _normalizar(query)
        extras: list[str] = []

        for termo, sinonimos in self._SINONIMOS.items():
            if termo in norm:
                extras.extend(sinonimos[:1])

        # Injeta fato mais relevante do usuário se disponível
        if fatos:
            extras.append(fatos[0][:60])

        partes = [query] + extras
        return " ".join(partes)[:280]

    async def transformar_com_flash(
        self,
        query: str,
        rota: str,
        historico: str = "",
    ) -> str:
        """Reescrita contextual via Gemini Flash para queries vagas ou com pronomes."""
        q_lower = query.lower().strip()
        palavras = q_lower.split()

        # Flexibiliza a detecção de pronomes ou query vaga que precisa de contexto
        tem_pronome = any(p in q_lower for p in ["isso", "ele", "ela", "aquilo", "esse", "este", "esta", "onde", "quando", "como", "qual", "quais", "cade", "cadê"])
        e_vaga = len(palavras) <= 5

        if not (tem_pronome or (e_vaga and rota in ("GERAL", "CALENDARIO", "EDITAL", "WIKI"))):
            return query

        if not historico:
            return query

        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            # Pega uma fatia generosa do histórico (até 1500 chars) para garantir contexto real
            historico_trecho = historico[-1500:]

            prompt = (
                f"<system_instruction>\n"
                f"Você é um especialista em reescrita de buscas para RAG da UEMA.\n"
                f"Analise o histórico recente da conversa e reescreva a última pergunta do usuário como uma query técnica direta, sem pronomes ou artigos, otimizada para pesquisa no banco de dados vetorial.\n"
                f"Responda APENAS com a query reescrita, sem markdown, sem explicações.\n"
                f"</system_instruction>\n\n"
                f"<historico>\n{historico_trecho}\n</historico>\n\n"
                f"<pergunta_usuario>{query}</pergunta_usuario>\n\n"
                f"Query Reescrita:"
            )

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=60,
                ),
            )
            reescrita = (response.text or "").strip()
            # Remove aspas se o modelo adicionar
            reescrita = reescrita.replace('"', '').replace("'", "")
            if len(reescrita) >= 3:
                logger.debug("🔄 Query transform: '%s' → '%s'", query[:50], reescrita[:50])
                return reescrita
        except Exception as e:
            logger.warning("⚠️ QueryTransform Flash falhou: %s", e)
        return query

    async def transformar(
        self,
        query: str,
        rota: str = "GERAL",
        fatos: list[str] | None = None,
        historico: str = "",
    ) -> TransformedQuery:
        """
        Gera a query transformada com variantes locais de busca exata e geral (Step-Back).
        """
        # 1. Identificar nomes próprios (ProperNounQueryStrategy)
        re_nome = re.compile(
            r'\b([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]+(?:\s+[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]+){1,4})\b'
        )
        re_titulos = re.compile(
            r'\b(Dr\.?|Dra\.?|Prof\.?|Profa\.?|Sr\.?|Sra\.?)\s+', re.I
        )

        nomes = re_nome.findall(query)
        nomes = [n for n in nomes if len(n.split()) >= 2]

        variants = []
        strategy = "passthrough"
        was_transformed = False
        keywords = []

        if nomes:
            nome_principal = max(nomes, key=len)
            nome_limpo = re_titulos.sub("", nome_principal).strip()
            variante_exata = f'"{nome_limpo}"'
            variante_sem_titulo = query.replace(nome_principal, nome_limpo)

            variants.append(variante_exata)
            if (variante_sem_titulo != query) and (variante_sem_titulo not in variants):
                variants.append(variante_sem_titulo)

            keywords.append(nome_limpo)
            strategy = "proper_noun"
            was_transformed = True

        # 2. Enriquecimento de palavras-chave UEMA
        query_enriquecida = self.transformar_local(query, fatos)
        if query_enriquecida != query:
            if strategy == "passthrough":
                strategy = "keyword_enrich"
            was_transformed = True
            primary = query_enriquecida
        else:
            primary = query

        # 3. Gerar query Step-Back (StepBackStrategy)
        texto_sb = query
        texto_sb = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", texto_sb)
        texto_sb = re.sub(r"\b20\d{2}[\./]\d{1,2}\b", "", texto_sb)
        texto_sb = re.sub(r"\b(br-ppi|br-q|br-dc|ir-ppi|cfo-pp|pcd)\b", "cota", texto_sb, flags=re.I)
        texto_sb = re.sub(r"\b([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ][a-záéíóúâêîôûãõç]{3,})\b", "", texto_sb)
        step_back = " ".join(texto_sb.split())
        if len(step_back) < 10:
            step_back = " ".join(query.split()[:3])

        # 4. Gemini Flash (reescrita externa opcional)
        query_llm = await self.transformar_com_flash(primary, rota, historico)
        if query_llm != primary:
            primary = query_llm
            strategy = "llm_transform"
            was_transformed = True

        return TransformedQuery(
            original=query,
            primary=primary,
            variants=variants,
            step_back=step_back,
            keywords=keywords,
            strategy_used=strategy,
            was_transformed=was_transformed,
        )


def _normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()
    return s.lower().strip()
