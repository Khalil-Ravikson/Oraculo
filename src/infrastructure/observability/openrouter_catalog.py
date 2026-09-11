"""
src/infrastructure/observability/openrouter_catalog.py
=====================================================
Catálogo público de modelos do OpenRouter, usado **só para consultar preço**.

O Oráculo não executa inferência pelo OpenRouter. Ele fala direto com Gemini,
DeepSeek e Groq (e com providers compatíveis com a API da OpenAI cadastrados
pelo painel). O que este módulo busca é a tabela de preços que o OpenRouter
publica, para cobrir modelo que ninguém cadastrou à mão em `/hub/llm-custo`.

## Duas responsabilidades, separadas de propósito

* `atualizar()` fala com a rede. Roda numa tarefa periódica.
* `preco_de()` só lê o Redis. Roda no caminho de toda resposta de LLM.

Misturar as duas colocaria uma requisição HTTP externa dentro do caminho de
responder uma pergunta sobre a wiki da UEMA — latência somada e dependência
de um serviço de fora para o bot funcionar. A separação é o requisito
"evite chamadas externas desnecessárias durante cada inferência".

## Normalização

O OpenRouter publica preço **por token**, como string. Convertemos para
`Decimal` por 1M de tokens, que é a unidade que o resto do projeto usa
(`llm_pricing`, `pricing.py`).

Campo ausente vira `None`, não zero: modelo sem preço de cache publicado não
é modelo com cache grátis.

## Nenhuma chave de API

O endpoint de modelos é público. Não enviamos credencial, e não há nada a
expor no frontend — o painel consome o resultado já normalizado pelo backend.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.infrastructure.observability.usage import PrecoNormalizado, PricingSource

logger = logging.getLogger(__name__)

REDIS_KEY = "pricing:openrouter:catalogo"
_TIMEOUT_S = 15.0
_UM_MILHAO = Decimal("1000000")

# Teto de sanidade para o payload. O endpoint devolve centenas de modelos;
# um corpo muito maior que isso indica outra coisa do outro lado.
_MAX_BYTES = 8 * 1024 * 1024


def _por_1m(valor) -> Decimal | None:
    """Preço por token (string do OpenRouter) → Decimal por 1M de tokens."""
    if valor in (None, "", "0"):
        # "0" é publicado por modelos gratuitos — é um preço legítimo, zero.
        return Decimal("0") if valor == "0" else None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if d < 0:
        return None
    return d * _UM_MILHAO


def _normalizar(dados: dict) -> dict[str, dict]:
    """`{id_do_modelo: {preços}}`, só com o que dá para usar."""
    catalogo: dict[str, dict] = {}
    for modelo in dados.get("data") or []:
        if not isinstance(modelo, dict):
            continue
        ident = modelo.get("id")
        if not ident or not isinstance(ident, str):
            continue

        precos = modelo.get("pricing") or {}
        if not isinstance(precos, dict):
            continue

        entrada = _por_1m(precos.get("prompt"))
        saida = _por_1m(precos.get("completion"))
        if entrada is None and saida is None:
            continue  # sem preço utilizável, não ocupa espaço no cache

        catalogo[ident.lower()] = {
            "input_por_1m": str(entrada) if entrada is not None else None,
            "output_por_1m": str(saida) if saida is not None else None,
            # Campos que só alguns modelos publicam.
            "cache_por_1m": (lambda v: str(v) if v is not None else None)(
                _por_1m(precos.get("input_cache_read"))
            ),
            "reasoning_por_1m": (lambda v: str(v) if v is not None else None)(
                _por_1m(precos.get("internal_reasoning"))
            ),
        }
    return catalogo


async def atualizar() -> int:
    """Busca o catálogo e grava no Redis. Devolve quantos modelos entraram.

    Nunca levanta: catálogo indisponível é um aviso, não uma falha — o
    resolvedor de preço simplesmente pula esta camada."""
    from src.infrastructure.settings import settings

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as cliente:
            resposta = await cliente.get(settings.OPENROUTER_CATALOG_URL)

        if resposta.status_code == 429:
            logger.warning("⚠️ [PRICING] OpenRouter respondeu 429 (limite de taxa) — catálogo mantido como está.")
            return 0
        if resposta.status_code != 200:
            logger.warning("⚠️ [PRICING] OpenRouter respondeu %s — catálogo mantido.", resposta.status_code)
            return 0
        if len(resposta.content) > _MAX_BYTES:
            logger.warning("⚠️ [PRICING] Catálogo do OpenRouter maior que o esperado (%d bytes) — ignorado.",
                           len(resposta.content))
            return 0

        dados = resposta.json()
    except Exception as exc:  # noqa: BLE001 — inclui JSON inválido e rede fora
        logger.warning("⚠️ [PRICING] Falha ao atualizar o catálogo do OpenRouter: %s", exc)
        return 0

    catalogo = _normalizar(dados if isinstance(dados, dict) else {})
    if not catalogo:
        logger.warning("⚠️ [PRICING] Catálogo do OpenRouter veio sem modelo utilizável — mantido o anterior.")
        return 0

    envelope = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "modelos": catalogo,
    }

    try:
        from src.infrastructure.redis_client import get_redis_text

        ttl = max(int(settings.OPENROUTER_CATALOG_TTL_H), 1) * 3600
        # TTL com folga sobre o intervalo de atualização: se uma execução
        # falhar, o catálogo anterior continua servindo em vez de sumir.
        get_redis_text().setex(REDIS_KEY, ttl * 2, json.dumps(envelope, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ [PRICING] Catálogo baixado mas não pôde ser gravado: %s", exc)
        return 0

    logger.info("💲 [PRICING] Catálogo do OpenRouter atualizado: %d modelos.", len(catalogo))
    return len(catalogo)


def _carregar() -> dict:
    try:
        from src.infrastructure.redis_client import get_redis_text

        bruto = get_redis_text().get(REDIS_KEY)
        if not bruto:
            return {}
        return json.loads(bruto if isinstance(bruto, str) else bruto.decode())
    except Exception:  # noqa: BLE001
        return {}


def _candidatos(provider: str, modelo: str) -> list[str]:
    """Como o mesmo modelo pode aparecer nomeado no OpenRouter.

    Lá os identificadores são `provedor/modelo` (`google/gemini-2.5-flash`),
    enquanto aqui guardamos só o nome (`gemini-2.5-flash`). O mapa cobre a
    diferença de nomenclatura entre os dois vocabulários."""
    modelo = (modelo or "").lower().strip()
    provider = (provider or "").lower().strip()

    prefixos = {
        "gemini": "google",
        "deepseek": "deepseek",
        "groq": "groq",
    }
    prefixo = prefixos.get(provider, provider)

    nomes = [modelo, f"{prefixo}/{modelo}"]
    # `models/gemini-embedding-001` → `gemini-embedding-001`
    if "/" in modelo:
        nomes.append(modelo.split("/", 1)[1])
    return [n for n in dict.fromkeys(nomes) if n]


def preco_de(provider: str, modelo: str) -> PrecoNormalizado | None:
    """Preço do cache local. **Não faz chamada de rede.**"""
    envelope = _carregar()
    modelos = envelope.get("modelos") or {}
    if not modelos:
        return None

    for nome in _candidatos(provider, modelo):
        entrada = modelos.get(nome)
        if not entrada:
            continue

        def _d(chave):
            valor = entrada.get(chave)
            try:
                return Decimal(valor) if valor is not None else None
            except (InvalidOperation, ValueError, TypeError):
                return None

        return PrecoNormalizado(
            input_por_1m=_d("input_por_1m"),
            output_por_1m=_d("output_por_1m"),
            cache_por_1m=_d("cache_por_1m"),
            reasoning_por_1m=_d("reasoning_por_1m"),
            origem=PricingSource.OPENROUTER,
            versao=envelope.get("atualizado_em", "")[:19],
        )
    return None


def estado() -> dict:
    """Resumo para o painel: quantos modelos e de quando."""
    envelope = _carregar()
    return {
        "modelos": len(envelope.get("modelos") or {}),
        "atualizado_em": envelope.get("atualizado_em"),
    }
