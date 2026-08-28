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
from src.api.routers.admin.admin_users_api import router as users_router

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

class EnvRequest(BaseModel):
    env: dict[str, str]

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
    try:
        from src.capabilities.persistence.prompt_config import tem_override_ativo
        from src.infrastructure.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            prompt_custom = await tem_override_ativo(session, "academic_knowledge", redis=r)
    except Exception:
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
                     f'verificado, criado_em FROM "pessoas" {where} '
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
                text(f'UPDATE "pessoas" SET {set_clause} WHERE id = :id'),
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

    from src.capabilities.persistence.prompt_config import tem_override_ativo
    from src.infrastructure.database.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            prompt_custom = await tem_override_ativo(session, "academic_knowledge", redis=r)
    except Exception:
        prompt_custom = bool(r.get("admin:system_prompt"))

    # Campos não sensíveis (sem API keys) para pré-preencher /hub/config.
    from src.infrastructure.settings import settings
    from src.infrastructure import dynamic_config

    return {
        "manutencao":     r.get("admin:maintenance_mode") == "1",
        "gemini_bloq":    r.get("admin:gemini_blocked") == "1",
        "prompt_custom":  prompt_custom,
        # GEMINI_MODEL é config dinâmica (Fase 1) — valor efetivo, não o .env
        "gemini_model":       dynamic_config.get_str("GEMINI_MODEL"),
        "deepseek_model":     settings.DEEPSEEK_MODEL,
        "groq_model":         settings.GROQ_MODEL,
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "evolution_url":      settings.EVOLUTION_BASE_URL,
        "evolution_instance": settings.EVOLUTION_INSTANCE_NAME,
        "redis_url":          settings.REDIS_URL,
        "dev_mode":           settings.DEV_MODE,
    }


@router.post("/system/prompt")
async def set_prompt(
    body:    PromptRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Sprint 2 (Fase 8): grava no catálogo versionado Postgres
    (`agent_prompts`, agente `academic_knowledge` — hoje o único que consome
    um system prompt de LLM) em vez da chave Redis crua `admin:system_prompt`."""
    from src.capabilities.persistence.prompt_config import publicar_novo_prompt, resetar_para_padrao
    from src.infrastructure.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        if body.prompt:
            await publicar_novo_prompt(session, "academic_knowledge", body.prompt, created_by=payload.sub)
            msg = f"✅ System prompt atualizado ({len(body.prompt)} chars)."
        else:
            await resetar_para_padrao(session, "academic_knowledge", created_by=payload.sub)
            msg = "✅ System prompt resetado para o padrão."
        await session.commit()

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=payload.sub, action="set_system_prompt",
        target=None, payload={"chars": len(body.prompt)}, resultado="ok",
    )

    return {"ok": True, "msg": msg}


# GEMINI_MODEL saiu daqui na Fase 1 (Plano A): virou config dinâmica
# (`config_dinamica` / /hub/config, sem restart). Editar via .env não teria
# efeito e divergiria do valor ativo.
_ENV_KEYS_PERMITIDAS = {
    "GEMINI_API_KEY", "EMBEDDING_PROVIDER",
    "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
    "GROQ_API_KEY", "GROQ_MODEL",
    "EVOLUTION_API_KEY", "EVOLUTION_BASE_URL", "EVOLUTION_INSTANCE_NAME",
    "DATABASE_URL", "REDIS_URL",
    "ADMIN_JWT_SECRET", "ADMIN_API_KEY", "ADMIN_NUMBERS",
    "LLAMA_CLOUD_API_KEY", "HF_TOKEN", "DEV_MODE",
}


def _gravar_env_inplace(caminho: str, updates: dict[str, str]) -> None:
    """Reescreve o `.env` no lugar (sem tempfile+rename). `dotenv.set_key`
    cria o arquivo temporário no DIRETÓRIO do `.env` (não no arquivo em si)
    — em produção `/app` é `root:root` (imagem), então mesmo com o `.env`
    liberado pro grupo do container, `set_key` batia em
    `PermissionError` ao criar o tempfile. Reescrever com `open(..., "w")`
    só precisa de permissão de escrita no PRÓPRIO arquivo, que é o que o
    bind-mount + `chgrp`/`chmod` do host já concede."""
    with open(caminho, "r", encoding="utf-8") as f:
        linhas = f.readlines()

    pendentes = dict(updates)
    saida = []
    for linha in linhas:
        stripped = linha.strip()
        chave = stripped.split("=", 1)[0].strip() if ("=" in stripped and not stripped.startswith("#")) else None
        if chave and chave in pendentes:
            valor = pendentes.pop(chave).replace("\\", "\\\\").replace('"', '\\"')
            saida.append(f'{chave}="{valor}"\n')
        else:
            saida.append(linha)

    if pendentes and saida and not saida[-1].endswith("\n"):
        saida[-1] += "\n"

    for chave, valor in pendentes.items():
        valor_escapado = valor.replace("\\", "\\\\").replace('"', '\\"')
        saida.append(f'{chave}="{valor_escapado}"\n')

    with open(caminho, "w", encoding="utf-8") as f:
        f.writelines(saida)


@router.post("/system/env")
async def set_env(
    body:    EnvRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Grava pares chave/valor no `.env` real do servidor (fonte que
    `Settings` — pydantic-settings — lê no boot). Requer reiniciar os
    serviços para o valor novo entrar em vigor. Só aceita chaves na
    allowlist — o body é um POST JSON, não confiar cegamente no que o
    cliente manda."""
    from src.infrastructure.paths import ENV_FILE

    updates = {k: v for k, v in body.env.items() if k in _ENV_KEYS_PERMITIDAS}
    if not updates:
        raise HTTPException(400, "Nenhuma chave válida para gravar.")

    _gravar_env_inplace(str(ENV_FILE), updates)
    escritas = list(updates.keys())

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=payload.sub, action="set_env",
        target=None, payload={"chaves": escritas}, resultado="ok",
    )

    return {"ok": True, "written": escritas}


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


@router.get("/celery/health")
async def celery_health(_: TokenPayload = Depends(require_admin_jwt)):
    from src.infrastructure.celery_app import celery_app
    try:
        result = celery_app.control.ping(timeout=3)
        workers = list(result[0].keys()) if result else []
        return {"ok": bool(workers), "workers": workers}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Configuração dinâmica (Plano A / Fase 1 — config_dinamica, migration 009)
#
# Postgres é a fonte de verdade; `dynamic_config` espelha no Redis para o
# caminho quente. Escrita com controle de concorrência otimista: o cliente
# manda a `versao` que tinha na tela; se o banco já avançou, devolve 409.
# ─────────────────────────────────────────────────────────────────────────────

class DynamicConfigSetRequest(BaseModel):
    chave:  str
    valor:  str
    versao: int


class DynamicConfigRevertRequest(BaseModel):
    para_versao: int
    versao:      int


@router.get("/config")
async def get_dynamic_config(_: TokenPayload = Depends(require_admin_jwt)):
    """Lista as chaves dinâmicas com valor/versão atuais. Reconcilia o
    espelho Redis a partir do Postgres (fonte de verdade) de passagem —
    toda abertura da tela sana eventual drift (§N item 2)."""
    from src.infrastructure import dynamic_config
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.dynamic_config_repository import DynamicConfigRepository

    try:
        async with AsyncSessionLocal() as session:
            linhas = await DynamicConfigRepository(session).listar()
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao ler config_dinamica: %s", exc)
        raise HTTPException(500, "Falha ao consultar a configuração dinâmica.")

    dynamic_config.espelhar_varias(linhas)
    return {"chaves": dynamic_config.snapshot(linhas)}


@router.get("/config/{chave}/historico")
async def get_dynamic_config_historico(
    chave: str,
    _: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure import dynamic_config
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.dynamic_config_repository import DynamicConfigRepository

    if chave not in dynamic_config.ALLOWED_DYNAMIC_KEYS:
        raise HTTPException(404, f"'{chave}' não é uma chave de configuração dinâmica.")

    try:
        async with AsyncSessionLocal() as session:
            hist = await DynamicConfigRepository(session).historico(chave)
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao ler histórico de '%s': %s", chave, exc)
        raise HTTPException(500, "Falha ao consultar o histórico.")

    return {
        "chave": chave,
        "historico": [
            {
                "versao": h["versao"],
                "valor_antigo": h["valor_antigo"],
                "valor_novo": h["valor_novo"],
                "atualizado_por": h["atualizado_por"],
                "atualizado_em": h["atualizado_em"].isoformat() if h["atualizado_em"] else None,
            }
            for h in hist
        ],
    }


async def _gravar_config_dinamica(
    chave: str, valor_bruto: str, versao_esperada: int, admin: str, acao: str,
) -> dict:
    from src.infrastructure import dynamic_config
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.dynamic_config_repository import (
        ConflitoDeVersao,
        DynamicConfigRepository,
    )

    try:
        valor, tipo = dynamic_config.normalizar_para_persistir(chave, valor_bruto)
    except dynamic_config.ChaveNaoPermitida:
        raise HTTPException(404, f"'{chave}' não é uma chave de configuração dinâmica.")
    except dynamic_config.ValorInvalido as exc:
        raise HTTPException(400, str(exc))

    try:
        async with AsyncSessionLocal() as session:
            nova_versao = await DynamicConfigRepository(session).upsert(
                chave, valor, tipo, versao_esperada=versao_esperada, atualizado_por=admin,
            )
            await session.commit()
    except ConflitoDeVersao as exc:
        raise HTTPException(409, {
            "erro": "conflito_de_versao",
            "mensagem": "Este valor mudou desde que você abriu a tela. Recarregue antes de salvar.",
            "versao_atual": exc.atual,
        })
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao gravar config_dinamica '%s': %s", chave, exc)
        raise HTTPException(500, "Falha ao gravar no Postgres. Tente novamente.")

    dynamic_config.espelhar_redis(chave, valor)

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=admin, action=acao,
        target=chave, payload={"valor": valor, "versao": nova_versao}, resultado="ok",
    )

    return {"ok": True, "chave": chave, "valor": valor, "tipo": tipo, "versao": nova_versao}


@router.post("/config")
async def set_dynamic_config(
    body:    DynamicConfigSetRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    return await _gravar_config_dinamica(
        body.chave, body.valor, body.versao, payload.sub, "set_dynamic_config",
    )


@router.post("/config/{chave}/reverter")
async def revert_dynamic_config(
    chave:   str,
    body:    DynamicConfigRevertRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure import dynamic_config
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.dynamic_config_repository import DynamicConfigRepository

    if chave not in dynamic_config.ALLOWED_DYNAMIC_KEYS:
        raise HTTPException(404, f"'{chave}' não é uma chave de configuração dinâmica.")

    try:
        async with AsyncSessionLocal() as session:
            alvo = await DynamicConfigRepository(session).valor_na_versao(chave, body.para_versao)
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao ler versão-alvo de '%s': %s", chave, exc)
        raise HTTPException(500, "Falha ao consultar o histórico.")

    if alvo is None:
        raise HTTPException(404, f"Versão {body.para_versao} não encontrada no histórico de '{chave}'.")

    return await _gravar_config_dinamica(
        chave, alvo, body.versao, payload.sub, "revert_dynamic_config",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Route/Workflow Registry (Plano A / Fase 2 — route_registry, migration 010)
#
# Colapsa os dicts hardcoded de rota→execução. Mesma mecânica do config
# dinâmico: optimistic lock (409), histórico com snapshot da linha, reverter.
# ─────────────────────────────────────────────────────────────────────────────

class RouteRegistrySetRequest(BaseModel):
    campos: dict
    versao: int


class RouteRegistryRevertRequest(BaseModel):
    para_versao: int
    versao:      int


@router.get("/routes")
async def get_route_registry(_: TokenPayload = Depends(require_admin_jwt)):
    """Lista as 11 rotas com valor/versão atuais e reconcilia o espelho Redis."""
    from src.infrastructure import route_registry
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.route_registry_repository import RouteRegistryRepository

    try:
        async with AsyncSessionLocal() as session:
            cfgs = await RouteRegistryRepository(session).listar()
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao ler route_registry: %s", exc)
        raise HTTPException(500, "Falha ao consultar o registro de rotas.")

    route_registry.espelhar_varias(cfgs)
    return {
        "rotas": route_registry.snapshot(cfgs),
        "nodes_validos": sorted(route_registry.NODES_ENTRYPOINT),
        "owners_validos": sorted(route_registry.OWNERS_VALIDOS),
    }


@router.get("/routes/{rota}/historico")
async def get_route_registry_historico(
    rota: str,
    _: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure import route_registry
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.route_registry_repository import RouteRegistryRepository

    if rota not in route_registry.ROTAS:
        raise HTTPException(404, f"'{rota}' não é uma rota conhecida.")

    try:
        async with AsyncSessionLocal() as session:
            hist = await RouteRegistryRepository(session).historico(rota)
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao ler histórico da rota '%s': %s", rota, exc)
        raise HTTPException(500, "Falha ao consultar o histórico.")

    return {
        "rota": rota,
        "historico": [
            {
                "versao": h["versao"],
                "snapshot": h["snapshot"],
                "atualizado_por": h["atualizado_por"],
                "atualizado_em": h["atualizado_em"].isoformat() if h["atualizado_em"] else None,
            }
            for h in hist
        ],
    }


async def _gravar_route_registry(
    rota: str, campos: dict, versao_esperada: int, admin: str, acao: str,
) -> dict:
    from src.infrastructure import route_registry
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.route_registry_repository import (
        ConflitoDeVersao,
        RouteRegistryRepository,
    )

    if rota not in route_registry.ROTAS:
        raise HTTPException(404, f"'{rota}' não é uma rota conhecida.")

    try:
        campos_validos = route_registry.validar_campos(campos)
    except route_registry.CamposInvalidos as exc:
        raise HTTPException(400, str(exc))

    try:
        async with AsyncSessionLocal() as session:
            cfg = await RouteRegistryRepository(session).upsert(
                rota, campos_validos, versao_esperada=versao_esperada, atualizado_por=admin,
            )
            await session.commit()
    except ConflitoDeVersao as exc:
        raise HTTPException(409, {
            "erro": "conflito_de_versao",
            "mensagem": "Esta rota mudou desde que você abriu a tela. Recarregue antes de salvar.",
            "versao_atual": exc.atual,
        })
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao gravar route_registry '%s': %s", rota, exc)
        raise HTTPException(500, "Falha ao gravar no Postgres. Tente novamente.")

    route_registry.espelhar_redis(cfg)   # write-through best-effort (swallows)

    from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
    await RedisAuditLog().registar(
        admin_id=admin, action=acao,
        target=rota, payload={"campos": campos_validos, "versao": cfg.versao}, resultado="ok",
    )

    return {"ok": True, "rota": rota, "versao": cfg.versao}


@router.post("/routes/{rota}")
async def set_route_registry(
    rota:    str,
    body:    RouteRegistrySetRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    return await _gravar_route_registry(
        rota, body.campos, body.versao, payload.sub, "set_route_registry",
    )


@router.post("/routes/{rota}/reverter")
async def revert_route_registry(
    rota:    str,
    body:    RouteRegistryRevertRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    from src.infrastructure import route_registry
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.route_registry_repository import RouteRegistryRepository

    if rota not in route_registry.ROTAS:
        raise HTTPException(404, f"'{rota}' não é uma rota conhecida.")

    try:
        async with AsyncSessionLocal() as session:
            snap = await RouteRegistryRepository(session).snapshot_da_versao(rota, body.para_versao)
    except Exception as exc:
        logger.warning("⚠️  [ADMIN] Falha ao ler versão-alvo da rota '%s': %s", rota, exc)
        raise HTTPException(500, "Falha ao consultar o histórico.")

    if snap is None:
        raise HTTPException(404, f"Versão {body.para_versao} não encontrada no histórico de '{rota}'.")

    campos = {k: snap[k] for k in route_registry.CAMPOS_EDITAVEIS if k in snap}
    return await _gravar_route_registry(
        rota, campos, body.versao, payload.sub, "revert_route_registry",
    )