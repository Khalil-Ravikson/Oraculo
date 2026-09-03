"""
infrastructure/dynamic_config.py — configuração dinâmica (runtime, sem restart)
================================================================================
Plano A / Fase 1 (docs/historico/plataforma_orientada_a_configuracao.md, Anexo I
+ §N). Generaliza o padrão já comprovado em `pricing.py` / `llm_factory.py`:

    Redis (espelho de leitura rápida)  →  Postgres (fonte de verdade)  →  default

O default é sempre o valor hardcoded de `settings.py` — qualquer falha de
leitura (Redis fora, Postgres fora, valor corrompido) cai nele sem levantar
exceção. Config nunca pode derrubar uma resposta real (mesma filosofia de toda
a telemetria deste projeto).

Dois níveis de API:

  get_bool / get_int / get_str        SÍNCRONO. Redis → default. É o que o
                                      caminho quente usa (adapters, workers).
                                      Sem `lru_cache` — reage a troca em runtime.

  aget_bool / aget_int / aget_str     ASSÍNCRONO. Redis → Postgres → default,
                                      com read-repair: um MISS no Redis relê o
                                      Postgres e reescreve o Redis antes de
                                      retornar (§N item 2) — a divergência
                                      Redis↔Postgres nunca fica permanente.

  hydrate_redis()                     Carrega todas as chaves do Postgres pro
                                      Redis. Chamado no boot do FastAPI e de
                                      cada processo worker Celery.

`ALLOWED_DYNAMIC_KEYS` é a allowlist de segurança (§G): o endpoint admin só
aceita gravar chaves que estão aqui, com o tipo declarado aqui — nunca chave
arbitrária vinda do cliente.

`tenant_id` NÃO é parâmetro de nenhuma função aqui: nas Fases 1-5 toda leitura
é global (§M). A coluna existe no schema, o código não a consulta.
"""
from __future__ import annotations

import logging

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

_PREFIXO_REDIS = "config:"

# chave -> tipo ("bool" | "int" | "str"). Allowlist de segurança + fonte do
# tipo para validação/coerção. Os defaults vêm de `settings.py` (mesmo nome).
ALLOWED_DYNAMIC_KEYS: dict[str, str] = {
    "DEV_TEST_NO_DB_WRITE":              "bool",
    "DEV_TEST_SKIP_REGISTRATION":        "bool",
    "FEATURE_LANGGRAPH_CELERY_DISPATCH": "bool",
    "GEMINI_MODEL":                      "str",
    "RAG_CACHE_TTL_SECONDS":             "int",
    "RAG_RERANKER_ENABLED":              "bool",
    "PARSER_PDF_PRIORIDADE":             "str",   # Fase 4
    "PARSER_DESABILITADOS":              "str",   # Fase 4
    "FEATURE_GRAPH_EXECUTOR_PILOTO":     "bool",  # Hub v2 Sprint 8 — nada lê no hot path ainda
}

_VERDADEIROS = {"1", "true", "t", "yes", "on", "sim"}
_FALSOS = {"0", "false", "f", "no", "off", "nao", "não"}

# Chaves cujo consumidor JÁ lê de `dynamic_config` nesta fase. As demais
# estão no schema/allowlist (Anexo I) mas continuam sendo lidas de `settings`
# pelos consumidores — o Hub as marca como "aguardando reconexão" para não
# dar a falsa impressão de que editá-las tem efeito imediato.
CHAVES_RECONECTADAS: frozenset[str] = frozenset({
    "GEMINI_MODEL", "RAG_CACHE_TTL_SECONDS", "RAG_RERANKER_ENABLED",
    "PARSER_PDF_PRIORIDADE", "PARSER_DESABILITADOS",
})


class ChaveNaoPermitida(ValueError):
    """`chave` fora de `ALLOWED_DYNAMIC_KEYS`."""


class ValorInvalido(ValueError):
    """`valor` incompatível com o `tipo` declarado da chave."""


# Mínimo aceitável por chave inteira (validação de escrita). TTL 0/negativo
# faria o Redis apagar a entrada na hora — cache desligado sem aviso.
_MIN_INT: dict[str, int] = {"RAG_CACHE_TTL_SECONDS": 1}

# Último recurso quando nem o valor nem o default são coercíveis.
_ZERO: dict[str, object] = {"bool": False, "int": 0, "str": ""}


# ─── Helpers de tipo ─────────────────────────────────────────────────────────

def _canonico(valor: object) -> str:
    """Forma canônica em string para persistir/espelhar (bool → 'true'/'false')."""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return str(valor)


def default_str(chave: str) -> str:
    """Default canônico da chave — o valor hardcoded de `settings.py`."""
    bruto = getattr(settings, chave, None)
    return _canonico(bruto) if bruto is not None else ""


def _coerce(valor_str: str, tipo: str) -> bool | int | str:
    if tipo == "bool":
        return str(valor_str).strip().lower() in _VERDADEIROS
    if tipo == "int":
        return int(str(valor_str).strip())
    return str(valor_str)


def _coerce_seguro(valor_str: str | None, tipo: str, chave: str) -> bool | int | str:
    """Coage `valor_str`; se falhar, tenta o default de `settings`; se ainda
    falhar, devolve o zero-value do tipo. Nunca levanta — é o contrato do
    caminho quente (mesma filosofia de `pricing.py`)."""
    for candidato in (valor_str, default_str(chave)):
        if candidato is None:
            continue
        try:
            return _coerce(candidato, tipo)
        except Exception:
            logger.warning(
                "⚠️  [DYNAMIC_CONFIG] Valor %r inválido para '%s' (%s).", candidato, chave, tipo,
            )
    return _ZERO[tipo]  # type: ignore[return-value]


def normalizar_para_persistir(chave: str, valor_bruto: object) -> tuple[str, str]:
    """Valida `valor_bruto` contra o tipo declarado de `chave` e devolve
    `(valor_canonico, tipo)`. Levanta `ChaveNaoPermitida` / `ValorInvalido`.
    Usado pelo endpoint admin — nunca confiar cegamente no body do POST."""
    tipo = ALLOWED_DYNAMIC_KEYS.get(chave)
    if tipo is None:
        raise ChaveNaoPermitida(f"'{chave}' não está em ALLOWED_DYNAMIC_KEYS.")

    if tipo == "bool":
        s = str(valor_bruto).strip().lower()
        if s in _VERDADEIROS:
            return "true", tipo
        if s in _FALSOS:
            return "false", tipo
        raise ValorInvalido(f"'{valor_bruto}' não é um booleano válido para {chave}.")

    if tipo == "int":
        try:
            n = int(str(valor_bruto).strip())
        except (TypeError, ValueError):
            raise ValorInvalido(f"'{valor_bruto}' não é um inteiro válido para {chave}.")
        minimo = _MIN_INT.get(chave)
        if minimo is not None and n < minimo:
            raise ValorInvalido(f"{chave} precisa ser >= {minimo} (recebido {n}).")
        return str(n), tipo

    texto = str(valor_bruto).strip()
    if not texto:
        raise ValorInvalido(f"{chave} não aceita valor vazio.")
    return texto, tipo


# ─── Redis (espelho) ─────────────────────────────────────────────────────────

def _redis_key(chave: str) -> str:
    return f"{_PREFIXO_REDIS}{chave}"


def _ler_redis(chave: str) -> str | None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        raw = get_redis_text().get(_redis_key(chave))
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode()
    except Exception as exc:
        logger.warning("⚠️  [DYNAMIC_CONFIG] Falha ao ler '%s' no Redis: %s", chave, exc)
        return None


def espelhar_redis(chave: str, valor_canonico: str) -> None:
    """Write-through do valor canônico no espelho Redis. Best-effort —
    uma falha aqui não invalida a escrita no Postgres (que já é a fonte de
    verdade); o próximo boot / read-repair reconcilia."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().set(_redis_key(chave), valor_canonico)
    except Exception as exc:
        logger.warning("⚠️  [DYNAMIC_CONFIG] Falha ao espelhar '%s' no Redis: %s", chave, exc)


# ─── Leitura síncrona (caminho quente): Redis → default ──────────────────────

def get_str(chave: str) -> str:
    return _coerce_seguro(_ler_redis(chave), "str", chave)  # type: ignore[return-value]


def get_bool(chave: str) -> bool:
    return _coerce_seguro(_ler_redis(chave), "bool", chave)  # type: ignore[return-value]


def get_int(chave: str) -> int:
    return _coerce_seguro(_ler_redis(chave), "int", chave)  # type: ignore[return-value]


# ─── Leitura assíncrona com read-repair: Redis → Postgres → default ──────────

async def _obter_postgres(chave: str) -> str | None:
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.dynamic_config_repository import DynamicConfigRepository
        async with AsyncSessionLocal() as session:
            linha = await DynamicConfigRepository(session).obter(chave)
        return linha["valor"] if linha else None
    except Exception as exc:
        logger.warning("⚠️  [DYNAMIC_CONFIG] Falha ao ler '%s' no Postgres: %s", chave, exc)
        return None


async def _aget(chave: str, tipo: str) -> bool | int | str:
    valor = _ler_redis(chave)
    if valor is None:
        # MISS no Redis → fonte de verdade + read-repair (§N item 2)
        valor = await _obter_postgres(chave)
        if valor is not None:
            espelhar_redis(chave, valor)
    return _coerce_seguro(valor, tipo, chave)


async def aget_str(chave: str) -> str:
    return await _aget(chave, "str")  # type: ignore[return-value]


async def aget_bool(chave: str) -> bool:
    return await _aget(chave, "bool")  # type: ignore[return-value]


async def aget_int(chave: str) -> int:
    return await _aget(chave, "int")  # type: ignore[return-value]


# ─── Hydrate / reconcile ─────────────────────────────────────────────────────

def espelhar_varias(linhas: list[dict]) -> int:
    """Espelha no Redis todas as `linhas` (dicts com `chave`/`valor`) que
    estão na allowlist. Usado pelo hydrate e pela reconciliação do GET admin."""
    n = 0
    for linha in linhas:
        if linha["chave"] in ALLOWED_DYNAMIC_KEYS:
            espelhar_redis(linha["chave"], linha["valor"])
            n += 1
    return n


def snapshot(linhas: list[dict]) -> list[dict]:
    """Une as linhas do Postgres com a allowlist: cada chave permitida vira um
    dict `{chave, tipo, valor, versao, default, atualizado_em, atualizado_por}`.
    Chaves ainda não gravadas aparecem com o default de `settings.py` e `versao: 0`."""
    por_chave = {linha["chave"]: linha for linha in linhas}
    saida = []
    for chave, tipo in ALLOWED_DYNAMIC_KEYS.items():
        linha = por_chave.get(chave)
        atualizado_em = linha["atualizado_em"] if linha else None
        saida.append({
            "chave": chave,
            "tipo": tipo,
            "valor": linha["valor"] if linha else default_str(chave),
            "versao": linha["versao"] if linha else 0,
            "default": default_str(chave),
            "reconectada": chave in CHAVES_RECONECTADAS,
            "atualizado_em": atualizado_em.isoformat() if atualizado_em else None,
            "atualizado_por": linha["atualizado_por"] if linha else None,
        })
    return saida


async def hydrate_redis() -> int:
    """Carrega o valor atual de todas as chaves do Postgres para o espelho
    Redis. Idempotente. Chamado no boot do FastAPI e de cada worker Celery
    para que o caminho quente síncrono nunca dependa de um read-repair.
    Nunca levanta — no pior caso o espelho fica vazio e `get_*` cai no default."""
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.dynamic_config_repository import DynamicConfigRepository
        async with AsyncSessionLocal() as session:
            linhas = await DynamicConfigRepository(session).listar()
    except Exception as exc:
        logger.warning("⚠️  [DYNAMIC_CONFIG] hydrate_redis falhou ao ler Postgres: %s", exc)
        return 0

    n = espelhar_varias(linhas)
    logger.info("🔧 [DYNAMIC_CONFIG] %d chave(s) espelhada(s) no Redis.", n)
    return n
