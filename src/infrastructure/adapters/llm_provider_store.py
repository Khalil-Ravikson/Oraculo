"""
src/infrastructure/adapters/llm_provider_store.py — provedores pelo painel
=========================================================================
Postgres (`llm_providers`, migration 017) é a fonte de verdade; um espelho
JSON no Redis (`admin:llm_providers`) é o que `llm_provider_registry` lê no
caminho quente (sync, ~1ms) — mesmo padrão write-through de `agents_set_llm`
e `pricing.py`.

A chave de API não passa por aqui: `api_key_env` é só o NOME da variável de
ambiente. O valor vem de `os.getenv` na hora de instanciar o provedor.

`seed_inicial()` insere as linhas dos 3 provedores de código (gemini/
deepseek/groq) na primeira vez — assim o painel já mostra o que existe hoje.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.models import LlmProvider

logger = logging.getLogger(__name__)

CHAVE_REDIS = "admin:llm_providers"
TIPOS_NATIVOS = ("gemini", "deepseek", "groq")
TIPOS_VALIDOS = (*TIPOS_NATIVOS, "openai_compat")


class NomeDuplicadoError(ValueError):
    pass


class ConfigInvalidaError(ValueError):
    pass


# ── Leitura ────────────────────────────────────────────────────────────────

async def listar(session) -> list[dict]:
    try:
        rows = (await session.execute(
            select(LlmProvider).where(LlmProvider.tenant_id.is_(None)).order_by(LlmProvider.nome)
        )).scalars().all()
        return [_row_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("⚠️  [LLM_PROVIDER_STORE] Falha ao listar: %s", exc)
        return []


async def obter(session, provider_id: int) -> dict | None:
    r = (await session.execute(
        select(LlmProvider).where(LlmProvider.id == provider_id, LlmProvider.tenant_id.is_(None))
    )).scalar_one_or_none()
    return _row_dict(r) if r else None


# ── Escrita ────────────────────────────────────────────────────────────────

async def criar(
    session, nome: str, tipo: str, *, base_url: str = "", api_key_env: str = "",
    modelos: list | None = None, modelo_default: str = "", admin: str | None = None,
) -> dict:
    nome = (nome or "").strip()
    if not nome:
        raise ConfigInvalidaError("Informe um nome para o provedor.")
    if tipo not in TIPOS_VALIDOS:
        raise ConfigInvalidaError(f"Tipo inválido: {tipo}.")
    if tipo == "openai_compat":
        if not base_url.strip():
            raise ConfigInvalidaError("Provedor compatível com OpenAI precisa de uma URL base.")
        if not modelo_default.strip():
            raise ConfigInvalidaError("Informe o modelo padrão.")

    from src.infrastructure.adapters.llm_provider_registry import registrados as _codigo
    if nome in _codigo():
        raise NomeDuplicadoError(f"Já existe um provedor de código chamado '{nome}'.")

    registro = LlmProvider(
        nome=nome, tipo=tipo, base_url=base_url.strip(), api_key_env=api_key_env.strip(),
        modelos=list(modelos or []), modelo_default=modelo_default.strip(),
        origem="painel", atualizado_por=admin,
    )
    session.add(registro)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise NomeDuplicadoError(f"Já existe um provedor chamado '{nome}'.") from exc
    return _row_dict(registro)


async def set_habilitado(session, provider_id: int, habilitado: bool, admin: str | None = None) -> bool:
    res = await session.execute(
        update(LlmProvider)
        .where(LlmProvider.id == provider_id, LlmProvider.tenant_id.is_(None))
        .values(habilitado=habilitado, atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc), versao=LlmProvider.versao + 1)
    )
    await session.flush()
    return res.rowcount > 0


async def remover(session, provider_id: int) -> bool:
    res = await session.execute(
        delete(LlmProvider).where(
            LlmProvider.id == provider_id, LlmProvider.tenant_id.is_(None),
            LlmProvider.origem == "painel",
        )
    )
    await session.flush()
    return res.rowcount > 0


async def seed_inicial(session, admin: str = "seed") -> int:
    """Insere as linhas dos 3 provedores de código, se ainda não existirem.
    Idempotente. Retorna quantas linhas criou."""
    from src.infrastructure.settings import settings

    existentes = {r["nome"] for r in await listar(session)}
    seeds = [
        dict(nome="gemini", tipo="gemini", base_url="",
             api_key_env="GEMINI_API_KEY", modelo_default=settings.GEMINI_MODEL,
             modelos=[settings.GEMINI_MODEL]),
        dict(nome="deepseek", tipo="deepseek", base_url=settings.DEEPSEEK_BASE_URL,
             api_key_env="DEEPSEEK_API_KEY", modelo_default=settings.DEEPSEEK_MODEL,
             modelos=[settings.DEEPSEEK_MODEL]),
        dict(nome="groq", tipo="groq", base_url=settings.GROQ_BASE_URL,
             api_key_env="GROQ_API_KEY", modelo_default=settings.GROQ_MODEL,
             modelos=[settings.GROQ_MODEL]),
    ]
    criados = 0
    for s in seeds:
        if s["nome"] in existentes:
            continue
        session.add(LlmProvider(origem="codigo", atualizado_por=admin, **s))
        criados += 1
    if criados:
        await session.flush()
    return criados


# ── Espelho Redis (caminho quente) ─────────────────────────────────────────

async def espelhar_redis(session) -> None:
    """Reescreve `admin:llm_providers` com o estado atual do Postgres.
    Chamado por todo endpoint que muda a tabela (write-through)."""
    from src.infrastructure.redis_client import get_redis_text

    dados = [
        {
            "nome": r["nome"], "tipo": r["tipo"], "base_url": r["base_url"],
            "api_key_env": r["api_key_env"], "modelo_default": r["modelo_default"],
            "modelos": r["modelos"], "habilitado": r["habilitado"], "origem": r["origem"],
        }
        for r in await listar(session)
    ]
    try:
        get_redis_text().set(CHAVE_REDIS, json.dumps(dados, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [LLM_PROVIDER_STORE] Falha ao espelhar no Redis: %s", exc)


def ler_espelho_redis() -> list[dict]:
    """Lê o espelho (sync — chamado pelo registry no caminho quente).
    Lista vazia em qualquer falha."""
    from src.infrastructure.redis_client import get_redis_text

    try:
        raw = get_redis_text().get(CHAVE_REDIS)
        return json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001
        return []


def _row_dict(r: LlmProvider) -> dict:
    tem_chave = bool(os.getenv(r.api_key_env, "")) if r.api_key_env else False
    return {
        "id": r.id, "nome": r.nome, "tipo": r.tipo, "base_url": r.base_url,
        "api_key_env": r.api_key_env, "api_key_definida": tem_chave,
        "modelos": r.modelos, "modelo_default": r.modelo_default,
        "habilitado": r.habilitado, "origem": r.origem, "versao": r.versao,
        "atualizado_em": r.atualizado_em.isoformat() if r.atualizado_em else None,
        "atualizado_por": r.atualizado_por,
    }
