"""
infrastructure/adapters/llm_provider_registry.py — Provider Registry (LLM)
================================================================================
Plano A / Fase 3 (docs/historico/plataforma_orientada_a_configuracao.md §D/§S).
Substitui o `if/elif` fechado de `llm_factory._instanciar` por um dict de
builders lazy-import, no molde de `rag/ingestion/parser_factory.py::_REGISTRY`.

Cada entrada carrega um manifesto pequeno e estático (§S): nome, versão da
interface implementada, o builder, e um `health_check()` opcional que o
circuit breaker (§O) e o Hub podem usar. NÃO é plugin architecture com
carregamento dinâmico de código externo — continua registro explícito, no
mesmo arquivo, pelo mesmo time.

COMO ADICIONAR UM PROVIDER:
  1. Criar o adapter implementando `ILLMProvider` (src/domain/ports/llm_Provider.py).
  2. Escrever um `_build_<nome>()` lazy-import + registrar em `_REGISTRY`.
  3. Adicionar preço em `observability/pricing.py::_PRECOS` e uma linha em
     `llm_pricing` (migration) — senão o custo é contabilizado como $0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from src.domain.ports.llm_Provider import ILLMProvider

logger = logging.getLogger(__name__)

_INTERFACE = "ILLMProvider/1"


@dataclass(frozen=True)
class ProviderManifest:
    nome: str
    interface_version: str
    builder: Callable[[str | None], ILLMProvider]
    health_check: Callable[[], bool] | None = None


# ─── Builders (lazy-import — só carrega a lib quando realmente precisa) ───────

def _build_gemini(modelo: str | None) -> ILLMProvider:
    from src.infrastructure.adapters.gemini_provider import GeminiProvider
    return GeminiProvider(model=modelo)


def _build_deepseek(modelo: str | None) -> ILLMProvider:
    from src.infrastructure.adapters.openai_compatible_provider import OpenAICompatibleProvider
    from src.infrastructure.settings import settings
    return OpenAICompatibleProvider(
        provider_name="deepseek",
        base_url=settings.DEEPSEEK_BASE_URL,
        api_key=settings.DEEPSEEK_API_KEY,
        model=modelo or settings.DEEPSEEK_MODEL,
    )


def _build_groq(modelo: str | None) -> ILLMProvider:
    from src.infrastructure.adapters.openai_compatible_provider import OpenAICompatibleProvider
    from src.infrastructure.settings import settings
    return OpenAICompatibleProvider(
        provider_name="groq",
        base_url=settings.GROQ_BASE_URL,
        api_key=settings.GROQ_API_KEY,
        model=modelo or settings.GROQ_MODEL,
    )


# ─── Health-checks (probe barato — chave configurada) ────────────────────────
# Deliberadamente NÃO faz chamada de rede: roda no /hub e no circuit breaker,
# não pode custar latência/tokens. "saudável" aqui = "tem credencial".

def _health_key(attr: str) -> Callable[[], bool]:
    def _check() -> bool:
        from src.infrastructure.settings import settings
        return bool(getattr(settings, attr, ""))
    return _check


_REGISTRY: dict[str, ProviderManifest] = {
    "gemini":   ProviderManifest("gemini",   _INTERFACE, _build_gemini,   _health_key("GEMINI_API_KEY")),
    "deepseek": ProviderManifest("deepseek", _INTERFACE, _build_deepseek, _health_key("DEEPSEEK_API_KEY")),
    "groq":     ProviderManifest("groq",     _INTERFACE, _build_groq,     _health_key("GROQ_API_KEY")),
}


# ─── API pública ────────────────────────────────────────────────────────────

def registrados() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def manifesto(nome: str) -> ProviderManifest | None:
    return _REGISTRY.get(nome)


def instanciar(provider_name: str, modelo: str | None = None) -> ILLMProvider:
    """Instancia o provider pelo registro. `ValueError` se desconhecido —
    fallback explícito (nunca silencioso), mesmo contrato de `parser_factory`."""
    m = _REGISTRY.get(provider_name)
    if m is None:
        raise ValueError(
            f"Provider LLM '{provider_name}' não registrado. Disponíveis: {', '.join(_REGISTRY)}"
        )
    return m.builder(modelo)


def health_check(nome: str) -> bool | None:
    """True/False do probe do manifesto; None se o provider não tem probe."""
    m = _REGISTRY.get(nome)
    if m is None or m.health_check is None:
        return None
    try:
        return bool(m.health_check())
    except Exception as exc:
        logger.warning("⚠️  [LLM_REGISTRY] health_check de '%s' falhou: %s", nome, exc)
        return False


def status() -> list[dict]:
    """Visão pro Hub: cada provider com versão de interface e saúde."""
    return [
        {"nome": nome, "interface": m.interface_version, "saude": health_check(nome)}
        for nome, m in _REGISTRY.items()
    ]
