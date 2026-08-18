"""
infrastructure/observability/pricing.py — tabela de preço por provider/modelo
================================================================================
Substitui a constante hardcoded e desatualizada que existia em
`agents/academic_knowledge/synthesis.py` (`_CUSTO_INPUT`/`_CUSTO_OUTPUT`,
comentário "Custo Gemini 2.5 Flash" mas com valores de preço antigo do
Gemini 1.5 Flash — achado registrado em `analise_custo_real_llm.md` §4).

Preços em USD por 1M tokens. Gemini pesquisado em 2026-08-15 (ver
`analise_custo_real_llm.md` §5 — havia divergência entre a página oficial e
rastreadores de terceiros). DeepSeek atualizado em 2026-08-15 com os
valores exatos da doc oficial (https://api-docs.deepseek.com/quick_start/pricing),
trazida pelo usuário. Preços de LLM mudam com frequência — revisar
periodicamente, não tratar como verdade eterna.

Groq: **modelo default incerto.** `settings.GROQ_MODEL` está em
`llama-3.3-70b-versatile`, mas a tabela oficial de "Production Models" do
Groq (console.groq.com/docs/model/..., trazida pelo usuário em 2026-08-15)
só lista `openai/gpt-oss-120b`/`openai/gpt-oss-20b` nessa categoria hoje —
Llama 3.3 70B pode ter saído da lista de produção. Preço de
`openai/gpt-oss-120b` adicionado como alternativa; decidir com o usuário
qual modelo Groq usar de fato antes de confiar neste número em produção.

`cache_por_1m` documentado mas **não aplicado ainda** — nenhum dos
adapters (`gemini_provider.py`/`openai_compatible_provider.py`) implementa
prompt caching hoje (side não pedido nesta rodada); o campo existe pra não
perder o dado quando isso for implementado.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrecoModelo:
    input_por_1m:  float        # USD por 1M tokens de entrada
    output_por_1m: float        # USD por 1M tokens de saída
    cache_por_1m:  float | None = None  # USD por 1M tokens de input em cache (não aplicado ainda)


# provider -> modelo -> preço. Chave "default" cobre modelo não listado
# explicitamente (evita KeyError se `settings.*_MODEL` mudar sem atualizar
# esta tabela — melhor subestimar/log de aviso do que quebrar a chamada).
_PRECOS: dict[str, dict[str, PrecoModelo]] = {
    "gemini": {
        "gemini-2.5-flash":      PrecoModelo(0.30, 2.50),
        "gemini-2.5-flash-lite": PrecoModelo(0.10, 0.40),
        "gemini-2.5-pro":        PrecoModelo(1.25, 10.00),
        "default":               PrecoModelo(0.30, 2.50),
    },
    "deepseek": {
        # Oficial (api-docs.deepseek.com/quick_start/pricing, 2026-08-15)
        "deepseek-chat": PrecoModelo(0.20, 1.20, cache_por_1m=0.02),
        "default":       PrecoModelo(0.20, 1.20, cache_por_1m=0.02),
    },
    "groq": {
        "llama-3.3-70b-versatile": PrecoModelo(0.59, 0.79),
        "openai/gpt-oss-120b":     PrecoModelo(0.15, 0.60),
        "openai/gpt-oss-20b":      PrecoModelo(0.075, 0.30),
        "default":                 PrecoModelo(0.59, 0.79),
    },
    # embeddings — só input, sem geração
    "gemini-embedding": {
        "gemini-embedding-001": PrecoModelo(0.15, 0.0),
        "default":              PrecoModelo(0.15, 0.0),
    },
}


def chave_redis_preco(provider: str, modelo: str) -> str:
    """Chave do cache Redis (write-through, sem TTL) que espelha
    `llm_pricing` (Postgres, migration 008) — mesmo padrão de
    `llm_factory._override_do_agente`. Usada tanto na leitura (aqui) quanto
    na escrita (endpoint `/hub/llm-pricing` em `hub.py`)."""
    return f"pricing:{provider}:{modelo}"


def _preco_do_cache(provider: str, modelo: str) -> PrecoModelo | None:
    """Lê o preço editável via hub no Redis (fonte de verdade é o Postgres
    `llm_pricing`; o Redis é só o espelho de leitura rápida do caminho
    quente, igual ao override de provider por agente). Sem exceção nunca
    propagada — telemetria não pode derrubar uma resposta real."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        raw = get_redis_text().get(chave_redis_preco(provider, modelo))
        if not raw:
            return None
        dados = json.loads(raw if isinstance(raw, str) else raw.decode())
        return PrecoModelo(
            input_por_1m=dados["input_por_1m"],
            output_por_1m=dados["output_por_1m"],
            cache_por_1m=dados.get("cache_por_1m"),
        )
    except Exception as exc:
        logger.warning("⚠️ [PRICING] Falha ao ler cache Redis de preço (%s/%s): %s", provider, modelo, exc)
        return None


def calcular_custo_usd(provider: str, modelo: str, tokens_in: int, tokens_out: int) -> float:
    """Calcula custo real em USD para uma chamada, dado provider+modelo reais.

    Checa primeiro o preço editável via `/hub/llm-custo` (Postgres
    `llm_pricing`, espelhado no Redis sem TTL — `_preco_do_cache`); sem
    cache, cai na tabela Python `_PRECOS` hardcoded abaixo.

    Nunca levanta exceção — provider/modelo desconhecidos caem no preço
    "default" do provider (ou 0.0 se o provider nem existir na tabela),
    porque isto roda no caminho de telemetria: uma tabela de preço
    desatualizada não pode derrubar uma resposta real ao usuário.
    """
    provider = provider or "gemini"
    preco = _preco_do_cache(provider, modelo)
    if preco is None:
        tabela = _PRECOS.get(provider, {})
        preco = tabela.get(modelo) or tabela.get("default")
    if preco is None:
        return 0.0
    return (tokens_in / 1_000_000 * preco.input_por_1m) + (tokens_out / 1_000_000 * preco.output_por_1m)
