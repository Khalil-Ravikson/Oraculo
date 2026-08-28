"""
infrastructure/adapters/llm_circuit_breaker.py — circuit breaker por provider LLM
================================================================================
Plano A / Fase 3 (docs/historico/plataforma_orientada_a_configuracao.md §O).
Camada fina de OBSERVAÇÃO sobre as chamadas LLM já instrumentadas por
`MonitoredLLMProvider` — não é reescrita do `llm_factory`.

Estado por provider, no Redis (compartilhado entre processos API/worker):
  cb:llm:{provider}:falhas       INCR com TTL de janela (settings.LLM_CB_JANELA_S)
  cb:llm:{provider}:aberto_em    epoch de quando o circuito abriu (TTL = cooldown)

Regras (§O):
  * ~N falhas consecutivas na janela → circuito ABRE + alerta (RedisAuditLog).
  * Depois de settings.LLM_CB_COOLDOWN_S → HALF-OPEN (deixa 1 tentativa passar).
  * 1 sucesso em half-open → FECHA (zera o contador).
  * O circuito ABERTO **não troca de provider automaticamente** — quem decide
    trocar continua sendo o admin via /hub. `permitir()` só informa + alerta.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_PREFIXO = "cb:llm:"

FECHADO = "fechado"
ABERTO = "aberto"
MEIO_ABERTO = "meio_aberto"


def _params() -> tuple[int, int, int]:
    from src.infrastructure.settings import settings
    return (
        int(getattr(settings, "LLM_CB_FALHAS_ABRE", 5)),
        int(getattr(settings, "LLM_CB_COOLDOWN_S", 60)),
        int(getattr(settings, "LLM_CB_JANELA_S", 120)),
    )


def _r():
    from src.infrastructure.redis_client import get_redis_text
    return get_redis_text()


def _k(provider: str, sufixo: str) -> str:
    return f"{_PREFIXO}{provider}:{sufixo}"


def estado(provider: str) -> str:
    """Estado atual, derivado do Redis. Nunca levanta — falha → FECHADO
    (fail-open: telemetria/CB não pode bloquear resposta ao usuário)."""
    _, cooldown, _janela = _params()
    try:
        aberto_em = _r().get(_k(provider, "aberto_em"))
        if not aberto_em:
            return FECHADO
        if time.time() - float(aberto_em) >= cooldown:
            return MEIO_ABERTO
        return ABERTO
    except Exception:
        return FECHADO


def permitir(provider: str) -> bool:
    """False só quando o circuito está ABERTO e ainda dentro do cooldown.
    ABERTO/MEIO_ABERTO nunca é imposto como bloqueio de verdade neste MVP
    (§O: alerta, não troca automática) — o caller loga e serve mesmo assim.
    Existe para o caller poder decidir."""
    return estado(provider) != ABERTO


def registrar_sucesso(provider: str) -> None:
    try:
        r = _r()
        if r.get(_k(provider, "aberto_em")) or r.get(_k(provider, "falhas")):
            r.delete(_k(provider, "aberto_em"), _k(provider, "falhas"))
            logger.info("✅ [LLM_CB] Circuito de '%s' fechado (sucesso).", provider)
    except Exception as exc:
        logger.warning("⚠️  [LLM_CB] Falha ao registrar sucesso de '%s': %s", provider, exc)


def registrar_falha(provider: str) -> None:
    limite, cooldown, janela = _params()
    try:
        r = _r()
        n = r.incr(_k(provider, "falhas"))
        if n == 1:
            r.expire(_k(provider, "falhas"), janela)
        if n >= limite and not r.get(_k(provider, "aberto_em")):
            r.set(_k(provider, "aberto_em"), str(time.time()), ex=cooldown)
            _alertar(provider, n, limite)
    except Exception as exc:
        logger.warning("⚠️  [LLM_CB] Falha ao registrar falha de '%s': %s", provider, exc)


def _alertar(provider: str, falhas: int, limite: int) -> None:
    logger.error(
        "🔴 [LLM_CB] Circuito ABERTO para '%s' (%d falhas >= %d). "
        "Provider NÃO trocado automaticamente — decida via /hub/llm-custo.",
        provider, falhas, limite,
    )
    try:
        import asyncio

        from src.infrastructure.adapters.redis_audit_log import RedisAuditLog

        coro = RedisAuditLog().registar(
            admin_id="sistema", action="llm_circuit_open",
            target=provider, payload={"falhas": falhas, "limite": limite}, resultado="alerta",
        )
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception:
        pass


def status() -> list[dict]:
    """Visão pro Hub."""
    return [
        {"provider": p, "estado": estado(p), "falhas": _falhas(p)}
        for p in ("gemini", "deepseek", "groq")
    ]


def _falhas(provider: str) -> int:
    try:
        return int(_r().get(_k(provider, "falhas")) or 0)
    except Exception:
        return 0
