# src/api/middleware/auth_middleware.py
"""
Middleware JWT para rotas protegidas do portal admin.

USO:
  # Em qualquer endpoint protegido:
  from src.api.middleware.auth_middleware import require_admin_jwt

  @router.get("/dados-sensiveis")
  async def dados(payload = Depends(require_admin_jwt)):
      ...

FLUXO:
  1. Extrai token do header Authorization: Bearer <token>
     OU do cookie "admin_token" (para o portal web)
  2. Verifica JWT via AdminAuthUseCase
  3. Verifica blocklist Redis (logout explícito)
  4. Injeta TokenPayload na rota

COOKIE vs HEADER:
  - Portal Web: cookie httpOnly (mais seguro, não acessível via JS)
  - API REST:   Authorization header (para integrações futuras)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status

from src.application.use_cases.admin_auth import TokenPayload, get_admin_auth

logger = logging.getLogger(__name__)


async def require_admin_jwt(
    authorization: Optional[str] = Header(None),
    admin_token:   Optional[str] = Cookie(None),
) -> TokenPayload:
    """
    Dependency FastAPI que valida JWT do admin.

    Aceita token via:
      1. Header: Authorization: Bearer <token>
      2. Cookie: admin_token=<token>

    Raises:
      HTTPException 401 se token ausente, inválido ou expirado.
    """
    token = None

    # Tenta extrair do header
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]

    # Fallback para cookie (portal web)
    if not token and admin_token:
        token = admin_token

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação ausente.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth = get_admin_auth()

    # Verifica blocklist (logout explícito)
    if auth.token_esta_bloqueado(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão encerrada. Faça login novamente.",
        )

    payload = auth.verificar_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


def get_current_token(
    authorization: Optional[str] = Header(None),
    admin_token:   Optional[str] = Cookie(None),
) -> Optional[str]:
    """Extrai o token bruto (para operações de logout)."""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return admin_token