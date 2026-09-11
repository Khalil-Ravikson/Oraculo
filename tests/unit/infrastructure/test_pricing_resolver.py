"""Telemetria de custo: resolução de preço e cálculo.

O defeito que motivou tudo: `calcular_custo_usd` devolvia `0.0` quando não
encontrava preço, então **"a chamada custou zero" e "não sei quanto custou"
eram o mesmo número**. Um provider novo, sem preço cadastrado, aparecia no
painel como gratuito.

Cobre os cenários pedidos na especificação, menos os de streaming — este
projeto não usa streaming de LLM (verificado: não há `generate_content_stream`
nem equivalente no código).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.infrastructure.observability import pricing_resolver as pr
from src.infrastructure.observability.usage import (
    CostStatus,
    PrecoNormalizado,
    PricingSource,
    TokenCountStatus,
)

OFICIAL = PrecoNormalizado(
    input_por_1m=Decimal("0.30"), output_por_1m=Decimal("2.50"),
    cache_por_1m=Decimal("0.03"), origem=PricingSource.OFICIAL, versao="llm_pricing",
)


# ── Cálculo ──────────────────────────────────────────────────────────────

def test_custo_de_entrada_e_saida():
    c = pr.calcular_custo(preco=OFICIAL, input_tokens=1_000_000, output_tokens=1_000_000)
    assert c.entrada == Decimal("0.30")
    assert c.saida == Decimal("2.50")
    assert c.total == Decimal("2.80")
    assert c.status is CostStatus.EXATO


def test_preco_por_milhao_escala_certo():
    """1000 tokens a 0,30 por milhão = 0,0003."""
    c = pr.calcular_custo(preco=OFICIAL, input_tokens=1000)
    assert c.entrada == Decimal("0.0003")


def test_cache_nao_e_cobrado_duas_vezes():
    """Tokens de cache são um SUBCONJUNTO da entrada. Cobrar os dois pelo
    preço cheio contaria o mesmo token duas vezes."""
    c = pr.calcular_custo(preco=OFICIAL, input_tokens=1_000_000, cached_input_tokens=400_000)

    # 600k a preço cheio + 400k a preço de cache.
    assert c.entrada == Decimal("0.18")   # 0.6 * 0.30
    assert c.cache == Decimal("0.012")    # 0.4 * 0.03


def test_reasoning_usa_o_preco_de_saida_quando_nao_ha_proprio():
    """Reasoning é token gerado. Sem preço específico publicado, o de saída é
    a aproximação honesta — e o status reflete isso pela origem."""
    c = pr.calcular_custo(preco=OFICIAL, reasoning_tokens=1_000_000)
    assert c.reasoning == Decimal("2.50")


def test_sem_preco_o_custo_e_desconhecido_nao_zero():
    """O coração do problema: ausência de preço não é gratuidade."""
    c = pr.calcular_custo(preco=PrecoNormalizado(origem=PricingSource.DESCONHECIDO),
                          input_tokens=5000, output_tokens=500)
    assert c.total is None
    assert c.status is CostStatus.DESCONHECIDO


def test_tokens_desconhecidos_tornam_o_custo_desconhecido():
    """Resposta sem campo `usage`: há preço, mas não há o que multiplicar."""
    c = pr.calcular_custo(preco=OFICIAL, token_count_status=TokenCountStatus.DESCONHECIDO)
    assert c.status is CostStatus.DESCONHECIDO
    assert c.total is None


def test_preco_de_fallback_marca_estimado():
    fallback = PrecoNormalizado(
        input_por_1m=Decimal("0.30"), output_por_1m=Decimal("2.50"),
        origem=PricingSource.OPENROUTER, versao="2026-09-11",
    )
    c = pr.calcular_custo(preco=fallback, input_tokens=1000, output_tokens=100)
    assert c.status is CostStatus.ESTIMADO
    assert c.origem is PricingSource.OPENROUTER


def test_token_estimado_torna_estimado_mesmo_com_preco_oficial():
    """Preço exato com contagem estimada continua sendo estimativa."""
    c = pr.calcular_custo(preco=OFICIAL, input_tokens=1000,
                          token_count_status=TokenCountStatus.ESTIMADO)
    assert c.status is CostStatus.ESTIMADO


def test_zero_tokens_nao_inventa_custo():
    c = pr.calcular_custo(preco=OFICIAL, input_tokens=0, output_tokens=0)
    assert c.total is None


# ── Valores inválidos ────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", [None, "abc", "-1", -5])
def test_valores_invalidos_viram_ausencia(valor):
    assert pr._dec(valor) is None


def test_zero_e_preco_valido():
    """Modelo gratuito tem preço zero, e isso é diferente de sem preço."""
    assert pr._dec(0) == Decimal("0")


# ── Cadeia de fallback ───────────────────────────────────────────────────

def test_oficial_vence_openrouter(monkeypatch):
    """Preço conferido por gente vence catálogo automático: se alguém editou
    no painel, foi porque o número de fora estava errado."""
    monkeypatch.setattr(pr, "_do_oficial", lambda p, m: OFICIAL)
    monkeypatch.setattr(pr, "_do_openrouter",
                        lambda p, m: PrecoNormalizado(input_por_1m=Decimal("9"),
                                                      origem=PricingSource.OPENROUTER))
    monkeypatch.setattr(pr, "_CADEIA", (pr._do_oficial, pr._do_openrouter))

    assert pr.resolver_preco("gemini", "x").origem is PricingSource.OFICIAL


def test_cai_para_openrouter_quando_nao_ha_oficial(monkeypatch):
    do_or = PrecoNormalizado(input_por_1m=Decimal("1"), output_por_1m=Decimal("2"),
                             origem=PricingSource.OPENROUTER)
    monkeypatch.setattr(pr, "_CADEIA", (lambda p, m: None, lambda p, m: do_or))
    assert pr.resolver_preco("novo", "modelo").origem is PricingSource.OPENROUTER


def test_camada_que_explode_nao_derruba_a_cadeia(monkeypatch):
    """Catálogo fora do ar não pode impedir o cálculo pelas outras fontes."""
    def explode(p, m):
        raise RuntimeError("catálogo fora")

    monkeypatch.setattr(pr, "_CADEIA", (explode, lambda p, m: OFICIAL))
    assert pr.resolver_preco("gemini", "x").origem is PricingSource.OFICIAL


def test_nenhuma_fonte_devolve_desconhecido(monkeypatch):
    monkeypatch.setattr(pr, "_CADEIA", (lambda p, m: None, lambda p, m: None))
    preco = pr.resolver_preco("inexistente", "modelo")
    assert preco.origem is PricingSource.DESCONHECIDO
    assert not preco.utilizavel


def test_preco_so_de_cache_nao_e_utilizavel(monkeypatch):
    """Preço sem entrada nem saída não serve para a conta principal."""
    so_cache = PrecoNormalizado(cache_por_1m=Decimal("0.03"), origem=PricingSource.OPENROUTER)
    monkeypatch.setattr(pr, "_CADEIA", (lambda p, m: so_cache,))
    assert pr.resolver_preco("x", "y").origem is PricingSource.DESCONHECIDO


# ── Adaptadores desligados ───────────────────────────────────────────────

def test_litellm_desligado_por_padrao():
    assert pr._do_litellm("gemini", "x") is None


def test_pricepertoken_desligado_por_padrao():
    assert pr._do_pricepertoken("gemini", "x") is None
