# src/application/use_cases/admin_auth.py
"""
Caso de Uso: Autenticação do Admin — JWT com sessão de 24h.

ARQUITETURA DE SEGURANÇA:
  1. Portal Web:
     - Login com username + password (definidos no .env)
     - Gera JWT (HS256, 24h) armazenado no cookie httpOnly
     - Middleware FastAPI valida o JWT em cada request /hub/*

  2. WhatsApp:
     - Admin é reconhecido pelo número (ADMIN_NUMBERS no .env)
     - Comandos críticos exigem ADMIN_CONFIRMATION_TOKEN adicional
     - O token pode ser um TOTP ou senha extra (double-check)

CLEAN ARCHITECTURE:
  Este use case não sabe nada sobre HTTP (FastAPI) nem sobre Redis.
  Recebe e retorna primitivos Python.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DTOs
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LoginResult:
    sucesso:        bool
    access_token:   Optional[str] = None
    expires_in:     int = 86400      # 24 horas em segundos
    erro:           Optional[str] = None


@dataclass
class TokenPayload:
    sub:       str    # username do admin
    iat:       int    # issued at (timestamp)
    exp:       int    # expiration (timestamp)
    is_admin:  bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Use Case
# ─────────────────────────────────────────────────────────────────────────────

class AdminAuthUseCase:
    """
    Gerencia autenticação do admin para o portal web.

    IMPLEMENTAÇÃO PRÓPRIA de JWT (sem dependência de python-jose ou PyJWT)
    usando apenas hmac + hashlib da stdlib Python — zero dependências extras.

    FORMATO DO TOKEN: base64url(header).base64url(payload).base64url(signature)
    """

    def __init__(self):
        from src.infrastructure.settings import settings
        self._username = settings.ADMIN_USERNAME
        self._password = settings.ADMIN_PASSWORD
        self._secret   = settings.ADMIN_JWT_SECRET or settings.ADMIN_API_KEY
        self._ttl      = 86400   # 24 horas

    def login(self, username: str, password: str) -> LoginResult:
        """
        Valida credenciais e retorna JWT.

        Comparação usando hmac.compare_digest para evitar timing attacks.
        """
        if not self._username or not self._password:
            logger.error("❌ ADMIN_USERNAME ou ADMIN_PASSWORD não configurados no .env")
            return LoginResult(sucesso=False, erro="Servidor não configurado corretamente.")

        credenciais_ok = (
            hmac.compare_digest(username, self._username)
            and hmac.compare_digest(
                hashlib.sha256(password.encode()).hexdigest(),
                hashlib.sha256(self._password.encode()).hexdigest(),
            )
        )

        if not credenciais_ok:
            logger.warning("🚫 Tentativa de login inválida: username='%s'", username)
            return LoginResult(sucesso=False, erro="Credenciais inválidas.")

        agora = int(time.time())
        token = self._criar_token(TokenPayload(
            sub=username, iat=agora, exp=agora + self._ttl,
        ))

        # Registra login no audit log
        self._registrar_login(username)

        logger.info("✅ Admin '%s' autenticado via portal web.", username)
        return LoginResult(sucesso=True, access_token=token, expires_in=self._ttl)

    def verificar_token(self, token: str) -> Optional[TokenPayload]:
        """
        Verifica JWT e retorna payload se válido.
        Retorna None se token expirado ou inválido.
        """
        try:
            payload = self._decodificar_token(token)
            if payload and payload.exp > int(time.time()):
                return payload
        except Exception as e:
            logger.debug("Token inválido: %s", e)
        return None

    def invalidar_token(self, token: str) -> None:
        """
        Adiciona token à blocklist Redis (logout explícito).
        TTL da entrada = tempo restante do token.
        """
        try:
            from src.infrastructure.redis_client import get_redis_text
            payload = self._decodificar_token(token)
            if payload:
                restante = max(0, payload.exp - int(time.time()))
                if restante > 0:
                    get_redis_text().setex(
                        f"admin:token:blocked:{token[-16:]}",
                        restante,
                        "1",
                    )
                    logger.info("✅ Token invalidado (logout).")
        except Exception as e:
            logger.debug("Falha ao invalidar token: %s", e)

    def token_esta_bloqueado(self, token: str) -> bool:
        """Verifica se o token foi explicitamente invalidado (logout)."""
        try:
            from src.infrastructure.redis_client import get_redis_text
            return bool(get_redis_text().get(f"admin:token:blocked:{token[-16:]}"))
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # JWT Manual (stdlib apenas)
    # ─────────────────────────────────────────────────────────────────────────

    def _criar_token(self, payload: TokenPayload) -> str:
        import base64, json
        header  = {"alg": "HS256", "typ": "JWT"}
        h_enc   = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=")
        p_dict  = {"sub": payload.sub, "iat": payload.iat, "exp": payload.exp, "adm": True}
        p_enc   = base64.urlsafe_b64encode(json.dumps(p_dict).encode()).rstrip(b"=")
        sig     = hmac.new(
            self._secret.encode(),
            f"{h_enc.decode()}.{p_enc.decode()}".encode(),
            hashlib.sha256,
        ).digest()
        s_enc   = base64.urlsafe_b64encode(sig).rstrip(b"=")
        return f"{h_enc.decode()}.{p_enc.decode()}.{s_enc.decode()}"

    def _decodificar_token(self, token: str) -> Optional[TokenPayload]:
        import base64, json

        partes = token.split(".")
        if len(partes) != 3:
            return None

        h_enc, p_enc, s_enc = partes

        # Verifica assinatura
        sig_esperada = hmac.new(
            self._secret.encode(),
            f"{h_enc}.{p_enc}".encode(),
            hashlib.sha256,
        ).digest()
        sig_recebida = base64.urlsafe_b64decode(s_enc + "==")

        if not hmac.compare_digest(sig_esperada, sig_recebida):
            return None

        payload = json.loads(base64.urlsafe_b64decode(p_enc + "=="))
        return TokenPayload(
            sub=payload["sub"], iat=payload["iat"], exp=payload["exp"],
        )

    def _registrar_login(self, username: str) -> None:
        """Registra login no audit log de forma silenciosa."""
        try:
            import json, datetime
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            entrada = json.dumps({
                "ts":      datetime.datetime.now().isoformat(),
                "action":  "admin_login_web",
                "admin":   username,
                "result":  "ok",
            }, ensure_ascii=False)
            r.lpush("audit:log", entrada)
            r.ltrim("audit:log", 0, 999)
            r.expire("audit:log", 86400 * 90)   # 90 dias
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_admin_auth: Optional[AdminAuthUseCase] = None


def get_admin_auth() -> AdminAuthUseCase:
    global _admin_auth
    if _admin_auth is None:
        _admin_auth = AdminAuthUseCase()
    return _admin_auth