"""
src/rag/query/transformer.py
-----------------------------
Orquestrador do pipeline de transformação de queries.

DESIGN:
  QueryTransformer recebe uma lista ordenada de IQueryStrategy.
  Para cada query, itera pelas estratégias na ordem e aplica a primeira
  cujo should_apply() retorna True (early-exit) OU acumula variantes
  (modo "accumulate" para RAG Fusion).

  Dois modos:
    first_match=True  → aplica só a primeira estratégia que aceita (padrão)
    first_match=False → aplica todas as estratégias que aceitam e
                        acumula as variantes resultantes (RAG Fusion mode)

FÁBRICAS:
  build_for_route() → cria um QueryTransformer pré-configurado para
  cada tipo de rota (CALENDARIO, EDITAL, CONTATOS, GERAL, WIKI).
  Isso elimina if/elif gigantes no core.py — o transformer já sabe
  qual estratégia usar dependendo do contexto.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .protocols import IQueryStrategy, RawQuery, TransformedQuery
from .strategies import (
    HyDEStrategy,
    KeywordEnrichStrategy,
    MultiQueryStrategy,
    PassthroughStrategy,
    RAGFusionStrategy,
    StepBackStrategy,
)

logger = logging.getLogger(__name__)

# Re-export para compatibilidade com código legado
QueryTransformada = TransformedQuery


class QueryTransformer:
    """
    Aplica estratégias de transformação de query em pipeline.

    Uso típico:
        transformer = QueryTransformer.build_for_route("CALENDARIO", llm=provider)
        result = transformer.transform(RawQuery(text="quando é a matrícula?", fatos_usuario=[...]))
        # result.all_queries → lista de queries para busca paralela
    """

    def __init__(
        self,
        strategies: list[IQueryStrategy],
        first_match: bool = True,
        name: str = "default",
    ):
        self._strategies = strategies
        self._first_match = first_match
        self._name = name

    def transform(self, query: RawQuery) -> TransformedQuery:
        """
        Aplica as estratégias e retorna a query transformada.
        Nunca lança exceção — em caso de falha retorna passthrough.
        """
        if not query.text.strip():
            return TransformedQuery(original=query.text, primary=query.text)

        try:
            if self._first_match:
                return self._apply_first_match(query)
            else:
                return self._apply_accumulate(query)
        except Exception as e:
            logger.error("❌ QueryTransformer '%s' falhou: %s", self._name, e)
            return TransformedQuery(
                original=query.text, primary=query.text,
                strategy_used="error_passthrough",
            )

    def _apply_first_match(self, query: RawQuery) -> TransformedQuery:
        for strategy in self._strategies:
            if strategy.should_apply(query):
                result = strategy.transform(query)
                logger.debug(
                    "🔄 [%s] Estratégia '%s' aplicada | '%.40s' → '%.40s'",
                    self._name, strategy.name, query.text, result.primary,
                )
                return result
        # Fallback: nenhuma estratégia aceitou
        return TransformedQuery(original=query.text, primary=query.text, strategy_used="no_match")

    def _apply_accumulate(self, query: RawQuery) -> TransformedQuery:
        """
        Acumula variantes de todas as estratégias que aceitam a query.
        Usado pelo RAG Fusion mode.
        """
        all_variants: list[str] = []
        primary = query.text
        strategies_used: list[str] = []

        for strategy in self._strategies:
            if strategy.should_apply(query):
                result = strategy.transform(query)
                strategies_used.append(strategy.name)
                if result.primary and result.primary not in all_variants:
                    all_variants.append(result.primary)
                for v in result.variants:
                    if v and v not in all_variants:
                        all_variants.append(v)

        if all_variants:
            primary = all_variants[0]
            variants = all_variants[1:]
        else:
            variants = []

        return TransformedQuery(
            original=query.text,
            primary=primary,
            variants=variants[:5],  # máximo 5 variantes para não explodir a busca
            strategy_used="+".join(strategies_used) or "no_strategy",
            was_transformed=len(all_variants) > 1,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Fábricas pré-configuradas por rota
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def build_for_route(
        cls,
        route: str,
        llm_provider=None,
    ) -> "QueryTransformer":
        """
        Cria um QueryTransformer otimizado para cada tipo de rota.

        ESTRATÉGIAS POR ROTA:
          CALENDARIO → KeywordEnrich + StepBack
            Datas e prazos precisam de termos exatos. HyDE seria caro demais
            para benefício marginal em queries já bem definidas.

          EDITAL → KeywordEnrich + MultiQuery (llm opcional) + StepBack
            Editais têm muitas siglas e números. Multi-query ajuda a cobrir
            diferentes aspectos de uma pergunta sobre cotas.

          CONTATOS → KeywordEnrich + RAGFusion (llm opcional)
            Nomes de setores variam muito (CTIC, TI, suporte, informática).
            RAGFusion gera perspectivas alternativas para melhor cobertura.

          WIKI → HyDE (llm opcional) + KeywordEnrich
            Documentação técnica de TI se beneficia de HyDE porque as
            queries dos alunos são muito distantes da linguagem técnica.

          GERAL → StepBack + KeywordEnrich
            Para queries sem rota definida, generalizamos primeiro.
        """
        route_upper = (route or "GERAL").upper()

        _builders: dict[str, list[IQueryStrategy]] = {
            "CALENDARIO": [
                KeywordEnrichStrategy(),
                StepBackStrategy(),
                PassthroughStrategy(),
            ],
            "EDITAL": [
                KeywordEnrichStrategy(),
                MultiQueryStrategy(llm_provider),
                StepBackStrategy(),
                PassthroughStrategy(),
            ],
            "CONTATOS": [
                KeywordEnrichStrategy(),
                RAGFusionStrategy(llm_provider, n_variantes=3),
                PassthroughStrategy(),
            ],
            "WIKI": [
                HyDEStrategy(llm_provider),
                KeywordEnrichStrategy(),
                PassthroughStrategy(),
            ],
            "GERAL": [
                StepBackStrategy(),
                KeywordEnrichStrategy(),
                PassthroughStrategy(),
            ],
        }

        strategies = _builders.get(route_upper, _builders["GERAL"])
        return cls(strategies=strategies, first_match=True, name=f"transformer_{route_upper.lower()}")

    @classmethod
    def build_rag_fusion(cls, llm_provider=None) -> "QueryTransformer":
        """
        Cria transformer no modo RAG Fusion (acumula variantes de múltiplas estratégias).
        Usar para queries onde a cobertura ampla é mais importante que precisão.
        """
        return cls(
            strategies=[
                KeywordEnrichStrategy(),
                RAGFusionStrategy(llm_provider, n_variantes=3),
                StepBackStrategy(),
            ],
            first_match=False,  # acumula todas as variantes
            name="transformer_rag_fusion",
        )