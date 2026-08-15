"""
infrastructure/observability/pricing.py — tabela de preço por provider/modelo
================================================================================
Substitui a constante hardcoded e desatualizada que existia em
`agents/academic_knowledge/synthesis.py` (`_CUSTO_INPUT`/`_CUSTO_OUTPUT`,
comentário "Custo Gemini 2.5 Flash" mas com valores de preço antigo do
Gemini 1.5 Flash — achado registrado em `analise_custo_real_llm.md` §4).

Preços em USD por 1M tokens, pesquisados em 2026-08-15 (ver fontes abaixo).
Preços de LLM mudam com frequência — revisar periodicamente, não tratar como
verdade eterna. Fonte oficial Gemini: https://ai.google.dev/gemini-api/docs/pricing
(há divergência entre essa página e rastreadores de mercado de terceiros —
ver `analise_custo_real_llm.md` §5 para o detalhe).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrecoModelo:
    input_por_1m:  float  # USD por 1M tokens de entrada
    output_por_1m: float  # USD por 1M tokens de saída


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
        "deepseek-chat": PrecoModelo(0.14, 0.28),
        "default":       PrecoModelo(0.14, 0.28),
    },
    "groq": {
        "llama-3.3-70b-versatile": PrecoModelo(0.59, 0.79),
        "default":                 PrecoModelo(0.59, 0.79),
    },
    # embeddings — só input, sem geração
    "gemini-embedding": {
        "gemini-embedding-001": PrecoModelo(0.15, 0.0),
        "default":              PrecoModelo(0.15, 0.0),
    },
}


def calcular_custo_usd(provider: str, modelo: str, tokens_in: int, tokens_out: int) -> float:
    """Calcula custo real em USD para uma chamada, dado provider+modelo reais.

    Nunca levanta exceção — provider/modelo desconhecidos caem no preço
    "default" do provider (ou 0.0 se o provider nem existir na tabela),
    porque isto roda no caminho de telemetria: uma tabela de preço
    desatualizada não pode derrubar uma resposta real ao usuário.
    """
    tabela = _PRECOS.get(provider or "gemini", {})
    preco = tabela.get(modelo) or tabela.get("default")
    if preco is None:
        return 0.0
    return (tokens_in / 1_000_000 * preco.input_por_1m) + (tokens_out / 1_000_000 * preco.output_por_1m)
