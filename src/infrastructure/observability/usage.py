"""
src/infrastructure/observability/usage.py
=========================================
O evento normalizado de uso de LLM, e o vocabulário que o acompanha.

Uma estrutura só, montada no ponto único de instrumentação
(`llm_factory.MonitoredLLMProvider`) e consumida por três destinos: a tabela
`metricas_llm`, as métricas Prometheus e o painel de custo.

## A distinção que motiva o módulo

Até 2026-09-11 o custo era um `float` e ponto. `calcular_custo_usd` devolvia
`0.0` quando não achava preço, então **"a chamada custou zero" e "não sei
quanto custou" eram o mesmo número** — e o painel somava os dois como se
fossem a mesma coisa. Um provider novo, sem preço na tabela, aparecia como
gratuito.

`CostStatus` existe para separar isso, e é por isso que o custo é
`Decimal | None` em vez de `float`: `None` significa desconhecido, e não
zero.

## Decimal, não float

Custo é dinheiro. `float` acumula erro ao somar milhões de linhas, e o
Postgres guarda `numeric`. Converter float→numeric na gravação já perdia
precisão antes de chegar ao banco.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import uuid4


class PricingSource(str, Enum):
    """De onde veio o preço aplicado. A ordem é a da cadeia de fallback."""

    OFICIAL = "official"        # `llm_pricing` no Postgres / tabela do código
    OPENROUTER = "openrouter"   # catálogo público, cacheado
    LITELLM = "litellm"         # tabela local do LiteLLM (adaptador opcional)
    PRICEPERTOKEN = "pricepertoken"  # fallback final (desligado por padrão)
    DESCONHECIDO = "unknown"    # nenhuma fonte tinha esse par provider+modelo


class CostStatus(str, Enum):
    EXATO = "exact"          # preço de fonte confiável e tokens reais
    ESTIMADO = "estimated"   # preço de fallback OU tokens contados por tokenizer
    DESCONHECIDO = "unknown"  # sem preço, ou sem tokens


class TokenCountStatus(str, Enum):
    REAL = "actual"          # o provider devolveu o uso
    ESTIMADO = "estimated"   # contado por tokenizer local
    DESCONHECIDO = "unknown"  # nem um nem outro


@dataclass(frozen=True)
class PrecoNormalizado:
    """Preço de um par provider+modelo, em USD por 1 milhão de tokens.

    `None` em qualquer campo significa "esta fonte não informa esse preço" —
    diferente de zero, que significa "é de graça". A distinção importa:
    modelo sem preço de cache não é modelo com cache grátis."""

    input_por_1m: Decimal | None = None
    output_por_1m: Decimal | None = None
    cache_por_1m: Decimal | None = None
    reasoning_por_1m: Decimal | None = None
    origem: PricingSource = PricingSource.DESCONHECIDO
    # Data ou versão do catálogo de onde o preço saiu — sem isso, recalcular
    # o histórico depois de uma atualização de preços dá outro número e a
    # auditoria não fecha.
    versao: str = ""

    @property
    def utilizavel(self) -> bool:
        """Dá para calcular alguma coisa? Um preço só de cache, sem entrada
        nem saída, não serve para a conta principal."""
        return self.input_por_1m is not None or self.output_por_1m is not None


@dataclass
class CustoDetalhado:
    """A conta aberta por componente. `None` = não foi possível calcular."""

    entrada: Decimal | None = None
    saida: Decimal | None = None
    cache: Decimal | None = None
    reasoning: Decimal | None = None
    status: CostStatus = CostStatus.DESCONHECIDO
    origem: PricingSource = PricingSource.DESCONHECIDO
    versao: str = ""

    @property
    def total(self) -> Decimal | None:
        partes = [p for p in (self.entrada, self.saida, self.cache, self.reasoning) if p is not None]
        if not partes:
            return None
        return sum(partes, Decimal("0"))


@dataclass
class UsoLLM:
    """Um evento de uso, normalizado.

    `request_id` nasce aqui e é a chave idempotente: um retry de task que
    reenvie o mesmo evento não duplica a linha (índice único parcial em
    `metricas_llm`, migration 027)."""

    provider: str
    model: str

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    token_count_status: TokenCountStatus = TokenCountStatus.REAL

    # Contexto do projeto — reaproveita os conceitos que já existem aqui, em
    # vez de inventar "organização"/"projeto" que o Oráculo não tem.
    user_id: str = ""
    rota: str = ""

    latencia_ms: int = 0
    status: Literal["ok", "erro"] = "ok"
    erro: str = ""

    request_id: str = field(default_factory=lambda: uuid4().hex)
    trace_id: str = ""
    criado_em: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    custo: CustoDetalhado = field(default_factory=CustoDetalhado)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def para_persistencia(self) -> dict:
        """Dicionário no vocabulário da tabela `metricas_llm`.

        Os nomes seguem o padrão do projeto (português nas colunas de
        domínio), não o do rascunho da especificação — é a convenção do
        repositório, registrada em `.claude.md`."""
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id or None,
            "user_id": self.user_id[-20:] if self.user_id else None,
            "rota": self.rota[:20] if self.rota else None,
            "provider": self.provider,
            "modelo": self.model,
            "tokens_entrada": self.input_tokens,
            "tokens_saida": self.output_tokens,
            "tokens_total": self.total_tokens,
            "tokens_cache": self.cached_input_tokens or None,
            "tokens_reasoning": self.reasoning_tokens or None,
            "custo_entrada": self.custo.entrada,
            "custo_saida": self.custo.saida,
            "custo_cache": self.custo.cache,
            "custo_reasoning": self.custo.reasoning,
            "custo_usd": self.custo.total,
            "moeda": "USD",
            "pricing_source": self.custo.origem.value,
            "cost_status": self.custo.status.value,
            "token_count_status": self.token_count_status.value,
            "pricing_version": self.custo.versao or None,
            "latencia_ms": self.latencia_ms,
            "status": self.status,
            # Limite de tamanho: mensagem de erro de provider pode vir com o
            # corpo inteiro da resposta, e isto vai para o banco.
            "erro": (self.erro[:500] or None) if self.erro else None,
        }
