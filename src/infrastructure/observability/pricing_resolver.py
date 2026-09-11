"""
src/infrastructure/observability/pricing_resolver.py
===================================================
Resolve o preço de um par provider+modelo e calcula o custo de uma chamada.

## A cadeia, e por que nesta ordem

```
1. Oficial   — `llm_pricing` (Postgres, editável em /hub/llm-custo) e a
               tabela do código. É o preço que o operador conferiu.
2. OpenRouter — catálogo público, cacheado no Redis por tarefa periódica.
               Cobre modelo que ninguém cadastrou à mão.
3. LiteLLM   — adaptador opcional, desligado por padrão.
4. PricePerToken — fallback final, desligado por padrão.
5. Nada      — custo fica DESCONHECIDO, não zero.
```

Preço conferido por gente vence catálogo automático: se alguém editou o
preço no painel, foi porque o número de fora estava errado para este caso.

## Nenhuma chamada de rede aqui

Este módulo roda no caminho de toda resposta de LLM. Ele só lê cache local
(Redis) e tabela em memória. Quem fala com o OpenRouter é a tarefa periódica
em `tasks/pricing_tasks.py`. Uma consulta HTTP por requisição de usuário
somaria latência à resposta e criaria dependência de um serviço externo para
responder uma pergunta sobre a wiki da UEMA.

## Falha nunca sobe

Telemetria é secundária à resposta. Qualquer erro em qualquer camada cai para
a próxima, e a última é "não sei o preço" — que é uma resposta válida e
registrada, não uma exceção.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from src.infrastructure.observability.usage import (
    CostStatus,
    CustoDetalhado,
    PrecoNormalizado,
    PricingSource,
    TokenCountStatus,
)

logger = logging.getLogger(__name__)

_UM_MILHAO = Decimal("1000000")


def _dec(valor) -> Decimal | None:
    """Converte para Decimal sem nunca levantar. `None` quando não dá.

    Valor negativo é tratado como ausente: preço negativo não existe, e
    aceitar um viraria crédito no total de custo."""
    if valor is None:
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d >= 0 else None


# ── Camada 1: preço oficial ──────────────────────────────────────────────

def _do_oficial(provider: str, modelo: str) -> PrecoNormalizado | None:
    """Reusa `pricing.py`, que já lê o espelho Redis de `llm_pricing` e cai
    na tabela do código. Não duplicamos essa lógica aqui."""
    try:
        from src.infrastructure.observability import pricing

        preco = pricing._preco_do_cache(provider, modelo)
        origem_versao = "llm_pricing"
        if preco is None:
            tabela = pricing._PRECOS.get(provider, {})
            preco = tabela.get(modelo) or tabela.get("default")
            origem_versao = "tabela_codigo"
        if preco is None:
            return None

        return PrecoNormalizado(
            input_por_1m=_dec(preco.input_por_1m),
            output_por_1m=_dec(preco.output_por_1m),
            cache_por_1m=_dec(preco.cache_por_1m),
            origem=PricingSource.OFICIAL,
            versao=origem_versao,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Preço oficial indisponível para %s/%s", provider, modelo, exc_info=True)
        return None


# ── Camada 2: catálogo do OpenRouter ─────────────────────────────────────

def _do_openrouter(provider: str, modelo: str) -> PrecoNormalizado | None:
    try:
        from src.infrastructure.observability import openrouter_catalog

        return openrouter_catalog.preco_de(provider, modelo)
    except Exception:  # noqa: BLE001
        logger.debug("Catálogo OpenRouter indisponível para %s/%s", provider, modelo, exc_info=True)
        return None


# ── Camadas 3 e 4: adaptadores opcionais ─────────────────────────────────

def _do_litellm(provider: str, modelo: str) -> PrecoNormalizado | None:
    """Desligado por padrão. O LiteLLM não é dependência do projeto, e os três
    providers em uso devolvem uso real de tokens — a razão principal para
    usá-lo (estimar tokens) não se aplica hoje.

    Para ligar: instale `litellm` e defina `FEATURE_PRICING_LITELLM=true`."""
    from src.infrastructure.settings import settings

    if not settings.FEATURE_PRICING_LITELLM:
        return None

    try:
        from litellm import model_cost  # type: ignore[import-not-found]

        entrada = model_cost.get(modelo) or model_cost.get(f"{provider}/{modelo}")
        if not entrada:
            return None

        # O LiteLLM guarda preço POR TOKEN; normalizamos para 1M.
        por_token_in = _dec(entrada.get("input_cost_per_token"))
        por_token_out = _dec(entrada.get("output_cost_per_token"))
        return PrecoNormalizado(
            input_por_1m=por_token_in * _UM_MILHAO if por_token_in is not None else None,
            output_por_1m=por_token_out * _UM_MILHAO if por_token_out is not None else None,
            origem=PricingSource.LITELLM,
            versao="litellm",
        )
    except ImportError:
        logger.warning(
            "⚠️ [PRICING] FEATURE_PRICING_LITELLM ligada mas o pacote `litellm` "
            "não está instalado — camada ignorada."
        )
        return None
    except Exception:  # noqa: BLE001
        logger.debug("LiteLLM falhou para %s/%s", provider, modelo, exc_info=True)
        return None


def _do_pricepertoken(provider: str, modelo: str) -> PrecoNormalizado | None:
    """Adaptador desligado, deliberadamente vazio.

    O PricePerToken não tem API pública nem arquivo estruturado documentado
    que eu tenha podido verificar. Implementar raspagem de HTML de um site de
    terceiro, sem confirmação de que o uso automatizado é permitido e sem
    contrato estável, é exatamente o que a especificação proíbe.

    A interface fica aqui para que ligar isso um dia seja preencher uma
    função, não redesenhar a cadeia. Para habilitar: implemente a leitura de
    uma fonte estável e defina `FEATURE_PRICING_PRICEPERTOKEN=true`."""
    from src.infrastructure.settings import settings

    if not settings.FEATURE_PRICING_PRICEPERTOKEN:
        return None

    logger.warning(
        "⚠️ [PRICING] FEATURE_PRICING_PRICEPERTOKEN está ligada, mas o adaptador "
        "não tem fonte implementada — ver a docstring de `_do_pricepertoken`."
    )
    return None


_CADEIA = (_do_oficial, _do_openrouter, _do_litellm, _do_pricepertoken)


def resolver_preco(provider: str, modelo: str) -> PrecoNormalizado:
    """Percorre a cadeia e devolve o primeiro preço utilizável.

    Nunca levanta. Sem preço em lugar nenhum, devolve um `PrecoNormalizado`
    vazio com origem `DESCONHECIDO` — e é o chamador que decide o que fazer,
    com a informação de que não sabe."""
    provider = (provider or "").strip() or "gemini"
    modelo = (modelo or "").strip()

    for camada in _CADEIA:
        try:
            preco = camada(provider, modelo)
        except Exception:  # noqa: BLE001 — a cadeia inteira é best-effort
            logger.debug("Camada %s falhou", camada.__name__, exc_info=True)
            continue
        if preco is not None and preco.utilizavel:
            return preco

    logger.info(
        "ℹ️ [PRICING] Sem preço para %s/%s em nenhuma fonte — custo ficará desconhecido.",
        provider, modelo,
    )
    return PrecoNormalizado(origem=PricingSource.DESCONHECIDO)


def calcular_custo(
    *,
    preco: PrecoNormalizado,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    token_count_status: TokenCountStatus = TokenCountStatus.REAL,
) -> CustoDetalhado:
    """A conta, aberta por componente.

    O status combina duas incertezas diferentes, e a pior manda:

    * o **preço** pode ser oficial (exato) ou de fallback (estimado);
    * a **contagem de tokens** pode ser real ou estimada por tokenizer.

    Preço oficial com token estimado continua sendo uma estimativa — por isso
    as duas entram na mesma decisão."""
    if not preco.utilizavel:
        return CustoDetalhado(status=CostStatus.DESCONHECIDO, origem=preco.origem, versao=preco.versao)

    if token_count_status is TokenCountStatus.DESCONHECIDO:
        return CustoDetalhado(status=CostStatus.DESCONHECIDO, origem=preco.origem, versao=preco.versao)

    def _parcela(tokens: int, por_1m: Decimal | None) -> Decimal | None:
        if por_1m is None or not tokens:
            return None
        return (Decimal(int(tokens)) / _UM_MILHAO) * por_1m

    # Tokens de cache são um SUBCONJUNTO da entrada nos providers que os
    # reportam: cobrar os dois pelo preço cheio contaria o mesmo token duas
    # vezes. Descontamos do total de entrada e cobramos à parte.
    entrada_cheia = max(int(input_tokens) - int(cached_input_tokens or 0), 0)

    custo = CustoDetalhado(
        entrada=_parcela(entrada_cheia, preco.input_por_1m),
        saida=_parcela(output_tokens, preco.output_por_1m),
        cache=_parcela(cached_input_tokens, preco.cache_por_1m),
        reasoning=_parcela(reasoning_tokens, preco.reasoning_por_1m or preco.output_por_1m),
        origem=preco.origem,
        versao=preco.versao,
    )

    preco_confiavel = preco.origem is PricingSource.OFICIAL
    tokens_confiaveis = token_count_status is TokenCountStatus.REAL
    custo.status = CostStatus.EXATO if (preco_confiavel and tokens_confiaveis) else CostStatus.ESTIMADO
    return custo
