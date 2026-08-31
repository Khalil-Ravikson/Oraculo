"""
src/services/channel_store.py — canais cadastrados pelo painel
=============================================================
Postgres (`canais`, migration 018) + espelho Redis (`admin:canais`).
Nesta fase só `whatsapp_evolution`, e só "conectar instância existente"
(status / QR / webhook) — não cria instância nova na Evolution.

A chave de API da Evolution NÃO passa por aqui: `config.api_key_env` é o
NOME da variável de ambiente; o valor vem de `os.getenv`.

O caminho de envio/recebimento de mensagem continua em `settings` nesta
fase. `seed_inicial()` cria a linha da instância atual a partir de
`settings.EVOLUTION_*` para o painel já mostrar o que existe.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.models import Canal
from src.infrastructure.security.ssrf_validator import URLInseguraError, validar_url_publica

logger = logging.getLogger(__name__)

CHAVE_REDIS = "admin:canais"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class NomeDuplicadoError(ValueError):
    pass


class ConfigInvalidaError(ValueError):
    pass


# ── Leitura ────────────────────────────────────────────────────────────────

async def listar(session) -> list[dict]:
    try:
        rows = (await session.execute(
            select(Canal).where(Canal.tenant_id.is_(None)).order_by(Canal.nome)
        )).scalars().all()
        return [_row_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("⚠️  [CHANNEL_STORE] Falha ao listar: %s", exc)
        return []


async def obter(session, canal_id: int) -> dict | None:
    r = (await session.execute(
        select(Canal).where(Canal.id == canal_id, Canal.tenant_id.is_(None))
    )).scalar_one_or_none()
    return _row_dict(r) if r else None


# ── Escrita ────────────────────────────────────────────────────────────────

async def criar(
    session, nome: str, *, base_url: str, api_key_env: str, instance: str,
    webhook_url: str = "", admin: str | None = None,
) -> dict:
    nome = (nome or "").strip()
    if not nome or not base_url.strip() or not instance.strip():
        raise ConfigInvalidaError("Informe nome, URL base e nome da instância.")
    validar_url_publica(base_url)  # propaga URLInseguraError
    if webhook_url:
        validar_url_publica(webhook_url)

    registro = Canal(
        nome=nome, tipo="whatsapp_evolution", webhook_url=webhook_url.strip(),
        config={"base_url": base_url.strip(), "api_key_env": api_key_env.strip(), "instance": instance.strip()},
        origem="painel", atualizado_por=admin,
    )
    session.add(registro)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise NomeDuplicadoError(f"Já existe um canal chamado '{nome}'.") from exc
    return _row_dict(registro)


async def set_habilitado(session, canal_id: int, habilitado: bool, admin: str | None = None) -> bool:
    res = await session.execute(
        update(Canal).where(Canal.id == canal_id, Canal.tenant_id.is_(None))
        .values(habilitado=habilitado, atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc), versao=Canal.versao + 1)
    )
    await session.flush()
    return res.rowcount > 0


async def set_webhook(session, canal_id: int, webhook_url: str, admin: str | None = None) -> bool:
    if webhook_url:
        validar_url_publica(webhook_url)
    res = await session.execute(
        update(Canal).where(Canal.id == canal_id, Canal.tenant_id.is_(None))
        .values(webhook_url=webhook_url.strip(), atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc), versao=Canal.versao + 1)
    )
    await session.flush()
    return res.rowcount > 0


async def remover(session, canal_id: int) -> bool:
    res = await session.execute(
        delete(Canal).where(Canal.id == canal_id, Canal.tenant_id.is_(None), Canal.origem == "painel")
    )
    await session.flush()
    return res.rowcount > 0


async def seed_inicial(session, admin: str = "seed") -> int:
    """Cria a linha da instância Evolution atual, se não existir. Idempotente."""
    from src.infrastructure.settings import settings

    if not settings.EVOLUTION_BASE_URL or not settings.EVOLUTION_INSTANCE_NAME:
        return 0
    existentes = {r["nome"] for r in await listar(session)}
    nome = settings.EVOLUTION_INSTANCE_NAME
    if nome in existentes:
        return 0
    session.add(Canal(
        nome=nome, tipo="whatsapp_evolution", origem="codigo", atualizado_por=admin,
        webhook_url=getattr(settings, "WHATSAPP_HOOK_URL", "") or "",
        config={
            "base_url": settings.EVOLUTION_BASE_URL,
            "api_key_env": "EVOLUTION_API_KEY",
            "instance": settings.EVOLUTION_INSTANCE_NAME,
        },
    ))
    await session.flush()
    return 1


# ── Espelho Redis ─────────────────────────────────────────────────────────

async def espelhar_redis(session) -> None:
    from src.infrastructure.redis_client import get_redis_text
    dados = await listar(session)
    try:
        get_redis_text().set(CHAVE_REDIS, json.dumps(dados, ensure_ascii=False, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [CHANNEL_STORE] Falha ao espelhar no Redis: %s", exc)


# ── Chamadas à Evolution (status / QR / webhook) ──────────────────────────

def _headers(canal: dict) -> dict:
    return {"Content-Type": "application/json", "apikey": os.getenv(canal["config"].get("api_key_env", ""), "")}


async def status_evolution(canal: dict) -> dict:
    """Estado da conexão da instância. Nunca lança — devolve
    `{"estado": "...", "erro": "..."}`."""
    cfg = canal.get("config", {})
    base = (cfg.get("base_url") or "").rstrip("/")
    inst = cfg.get("instance") or ""
    if not base or not inst:
        return {"estado": "sem_configuracao"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{base}/instance/connectionState/{inst}", headers=_headers(canal))
        if r.status_code == 404:
            return {"estado": "nao_encontrada"}
        r.raise_for_status()
        estado = (r.json().get("instance") or {}).get("state", "desconhecido")
        return {"estado": estado}
    except Exception as exc:  # noqa: BLE001
        return {"estado": "erro", "erro": str(exc)[:200]}


async def qrcode_evolution(canal: dict) -> dict:
    """Dispara/retorna o QR de pareamento (base64) da instância."""
    cfg = canal.get("config", {})
    base = (cfg.get("base_url") or "").rstrip("/")
    inst = cfg.get("instance") or ""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{base}/instance/connect/{inst}", headers=_headers(canal))
        r.raise_for_status()
        d = r.json()
        return {"qr_base64": d.get("base64") or d.get("qrcode", {}).get("base64"), "code": d.get("code") or d.get("pairingCode")}
    except Exception as exc:  # noqa: BLE001
        return {"erro": str(exc)[:200]}


async def set_webhook_evolution(canal: dict, webhook_url: str) -> dict:
    cfg = canal.get("config", {})
    base = (cfg.get("base_url") or "").rstrip("/")
    inst = cfg.get("instance") or ""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{base}/webhook/set/{inst}", headers=_headers(canal),
                json={"webhook": {"enabled": True, "url": webhook_url}},
            )
        r.raise_for_status()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "erro": str(exc)[:200]}


def _row_dict(r: Canal) -> dict:
    cfg = r.config or {}
    return {
        "id": r.id, "nome": r.nome, "tipo": r.tipo,
        "config": cfg, "webhook_url": r.webhook_url,
        "instance": cfg.get("instance", ""), "base_url": cfg.get("base_url", ""),
        "api_key_env": cfg.get("api_key_env", ""),
        "api_key_definida": bool(os.getenv(cfg.get("api_key_env", ""), "")),
        "habilitado": r.habilitado, "origem": r.origem, "versao": r.versao,
        "atualizado_em": r.atualizado_em.isoformat() if r.atualizado_em else None,
        "atualizado_por": r.atualizado_por,
    }
