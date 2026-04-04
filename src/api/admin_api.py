# src/api/admin_api.py
"""
API REST do Admin — endpoints com autenticação JWT.

ROTAS:
  POST /api/admin/login          → autenticar (retorna JWT)
  POST /api/admin/logout         → invalidar token atual
  GET  /api/admin/me             → dados do admin logado
  GET  /api/admin/audit          → log de auditoria
  GET  /api/admin/metrics        → métricas do sistema
  GET  /api/admin/users          → listar utilizadores
  PATCH /api/admin/users/{id}    → atualizar utilizador
  GET  /api/admin/system         → flags de sistema (manutenção, etc.)
  POST /api/admin/system/prompt  → alterar system prompt
  POST /api/admin/system/maintenance → ligar/desligar manutenção
  GET  /api/admin/audit          → log de auditoria
  DELETE /api/admin/cache        → limpar cache semântico

Separado do hub.py (MVC):
  hub.py      → serve o HTML/templates do portal
  admin_api.py → REST API JSON (usada pelo frontend JS do hub)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.middleware.auth_middleware import (
    TokenPayload,
    get_current_token,
    require_admin_jwt,
)
from src.application.use_cases.admin_auth import get_admin_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["Admin API"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas de Request
# ─────────────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class PromptRequest(BaseModel):
    prompt: str   # "" para resetar ao padrão

class MaintenanceRequest(BaseModel):
    ativo: bool

class UserUpdateRequest(BaseModel):
    role:        str | None = None
    status:      str | None = None
    verificado:  bool | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Auth endpoints (públicos)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, response: Response):
    """
    Autentica o admin e retorna JWT.
    Seta cookie httpOnly para o portal web E retorna token no body para uso via fetch.
    """
    auth   = get_admin_auth()
    result = auth.login(body.username, body.password)

    if not result.sucesso:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.erro,
        )

    # Cookie httpOnly (portal web — mais seguro)
    response.set_cookie(
        key="admin_token",
        value=result.access_token,
        max_age=result.expires_in,
        httponly=True,
        samesite="lax",
        secure=False,   # True em produção com HTTPS
    )

    return {
        "access_token": result.access_token,
        "token_type":   "bearer",
        "expires_in":   result.expires_in,
    }


@router.post("/logout")
async def logout(
    response: Response,
    token:    str | None = Depends(get_current_token),
):
    """Invalida o token atual e limpa o cookie."""
    if token:
        get_admin_auth().invalidar_token(token)

    response.delete_cookie("admin_token")
    return {"ok": True, "msg": "Sessão encerrada."}


@router.get("/me")
async def me(payload: TokenPayload = Depends(require_admin_jwt)):
    """Retorna dados do admin logado."""
    return {
        "username": payload.sub,
        "is_admin": payload.is_admin,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Audit Log
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit")
async def audit(
    limit:  int = 50,
    offset: int = 0,
    _: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    entries = await RedisAuditLog().listar(limit=limit, offset=offset)
    return {"entries": entries, "total": len(entries)}


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/metrics")
async def metrics(_: TokenPayload = Depends(require_admin_jwt)):
    """Métricas em tempo real — consultadas pelo dashboard a cada 2-3s."""
    from src.infrastructure.redis_client import get_redis_text, redis_ok
    r    = get_redis_text()
    logs = []

    try:
        raw  = r.lrange("monitor:logs", 0, 199)
        logs = [json.loads(l) for l in raw]
    except Exception:
        pass

    total    = len(logs)
    tok_med  = sum(l.get("tokens_total", 0) for l in logs[:50]) // max(len(logs[:50]), 1)
    lat_med  = sum(l.get("latencia_ms", 0) for l in logs[:50]) // max(len(logs[:50]), 1)

    por_rota: dict[str, int] = {}
    por_role: dict[str, int] = {}
    for l in logs:
        r_ = l.get("route", l.get("rota", "?"))
        ro = l.get("role", "?")
        por_rota[r_] = por_rota.get(r_, 0) + 1
        por_role[ro] = por_role.get(ro, 0) + 1

    # Flags de sistema
    manutencao = r.get("admin:maintenance_mode") == "1"
    api_bloq   = r.get("admin:gemini_blocked") == "1"
    prompt_custom = bool(r.get("admin:system_prompt"))

    return {
        "redis_ok":       redis_ok(),
        "total_msgs":     total,
        "tokens_medio":   tok_med,
        "latencia_media": lat_med,
        "por_rota":       por_rota,
        "por_role":       por_role,
        "manutencao":     manutencao,
        "gemini_bloq":    api_bloq,
        "prompt_custom":  prompt_custom,
        "atividade":      logs[:20],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Utilizadores
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users")
async def listar_users(
    limit:  int = 50,
    role:   str = "",
    status: str = "",
    _: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    where_clauses, params = [], {}
    if role:
        where_clauses.append("role = :role")
        params["role"] = role
    if status:
        where_clauses.append("status = :status")
        params["status"] = status

    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    params["limit"] = min(limit, 200)

    try:
        async with AsyncSessionLocal() as s:
            rows = await s.execute(
                text(f'SELECT id, nome, email, telefone, role, status, curso, '
                     f'verificado, criado_em FROM "Pessoas" {where} '
                     f'ORDER BY criado_em DESC LIMIT :limit'),
                params,
            )
            cols    = rows.keys()
            pessoas = [dict(zip(cols, r)) for r in rows.fetchall()]
            for p in pessoas:
                for k, v in p.items():
                    if hasattr(v, "isoformat"):
                        p[k] = v.isoformat()
    except Exception as e:
        raise HTTPException(500, str(e))

    return {"users": pessoas, "total": len(pessoas)}


@router.patch("/users/{pessoa_id}")
async def atualizar_user(
    pessoa_id: int,
    body:      UserUpdateRequest,
    payload:   TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Nenhum campo para atualizar.")

    allowed = {"role", "status", "verificado"}
    updates = {k: v for k, v in updates.items() if k in allowed}

    try:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = pessoa_id
        async with AsyncSessionLocal() as s:
            await s.execute(
                text(f'UPDATE "Pessoas" SET {set_clause} WHERE id = :id'),
                updates,
            )
            await s.commit()
    except Exception as e:
        raise HTTPException(500, str(e))

    # Audit
    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=payload.sub, action="update_user",
        target=str(pessoa_id), payload=body.model_dump(), resultado="ok",
    )

    return {"ok": True, "updated": list(updates.keys())}


# ─────────────────────────────────────────────────────────────────────────────
# Sistema (flags, prompt, manutenção)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/system")
async def system_flags(_: TokenPayload = Depends(require_admin_jwt)):
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()
    return {
        "manutencao":     r.get("admin:maintenance_mode") == "1",
        "gemini_bloq":    r.get("admin:gemini_blocked") == "1",
        "prompt_custom":  r.get("admin:system_prompt") or "",
    }


@router.post("/system/prompt")
async def set_prompt(
    body:    PromptRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

    if body.prompt:
        r.set("admin:system_prompt", body.prompt)
        msg = f"✅ System prompt atualizado ({len(body.prompt)} chars)."
    else:
        r.delete("admin:system_prompt")
        msg = "✅ System prompt resetado para o padrão."

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=payload.sub, action="set_system_prompt",
        target=None, payload={"chars": len(body.prompt)}, resultado="ok",
    )

    return {"ok": True, "msg": msg}


@router.post("/system/maintenance")
async def set_maintenance(
    body:    MaintenanceRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure.redis_client import get_redis_text
    r = get_redis_text()

    if body.ativo:
        r.set("admin:maintenance_mode", "1")
        msg = "🔧 Modo manutenção ATIVADO."
    else:
        r.delete("admin:maintenance_mode")
        msg = "✅ Modo manutenção DESATIVADO."

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=payload.sub, action="set_maintenance",
        target=None, payload={"ativo": body.ativo}, resultado="ok",
    )

    return {"ok": True, "msg": msg}


@router.delete("/cache")
async def clear_cache(
    rota:    str = "",
    payload: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure.semantic_cache import invalidar_cache_rota
    from src.domain.entities import Rota

    if rota:
        n = invalidar_cache_rota(rota.upper())
    else:
        n = sum(invalidar_cache_rota(r.value) for r in Rota)

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=payload.sub, action="clear_cache",
        target=rota or "all", payload={"deleted": n}, resultado="ok",
    )

    return {"ok": True, "deleted": n, "rota": rota or "all"}