# src/infrastructure/settings.py
"""
Settings do Oráculo v3 — Campos adicionados para sistema admin completo.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.infrastructure.paths import ENV_FILE


class Settings(BaseSettings):
    # ── Ambiente ──────────────────────────────────────────────────
    DEV_MODE:      bool = False
    LOG_LEVEL:     str  = "INFO"

    # ── Banco de Dados ────────────────────────────────────────────
    DATABASE_URL:  str  = "postgresql+asyncpg://user:pass@localhost/oraculo"
    REDIS_URL:     str  = "redis://redis:6379/0"

    # ── LLM ──────────────────────────────────────────────────────
    GEMINI_API_KEY:    str   = ""
    GEMINI_MODEL:      str   = "gemini-2.0-flash-lite"
    GEMINI_TEMP:       float = 0.2
    GEMINI_MAX_TOKENS: int   = 1024

    # ── WhatsApp ──────────────────────────────────────────────────
    EVOLUTION_BASE_URL:      str = ""
    EVOLUTION_API_KEY:       str = ""
    EVOLUTION_INSTANCE_NAME: str = ""
    WHATSAPP_HOOK_URL:       str = ""

    # ── RAG ───────────────────────────────────────────────────────
    PDF_PARSER:           str = "pymupdf"
    LLAMA_CLOUD_API_KEY:  str = ""
    HF_TOKEN:             str = ""
    DATA_DIR:             str = "/app/dados"
    MAX_HISTORY_MESSAGES: int = 20

    # ── Admin — Portal Web ────────────────────────────────────────
    # Credenciais do portal admin (login/senha)
    ADMIN_USERNAME:   str = "admin"
    ADMIN_PASSWORD:   str = ""          # OBRIGATÓRIO em produção

    # Chave secreta para assinar JWT (se vazio, usa ADMIN_API_KEY)
    ADMIN_JWT_SECRET: str = ""

    # Chave de compatibilidade (mantida para endpoints existentes)
    ADMIN_API_KEY:    str = ""

    # ── Admin — WhatsApp ──────────────────────────────────────────
    # Números do admin (reconhecidos automaticamente como admin)
    ADMIN_NUMBERS:   str = ""    # "5598999990001,5598999990002"

    # Token extra para comandos críticos via WhatsApp (double-check)
    # Pode ser uma senha ou código TOTP
    ADMIN_CONFIRMATION_TOKEN: str = ""

    # ── RBAC ──────────────────────────────────────────────────────
    STUDENT_NUMBERS: str = ""

    # ── Embedding ─────────────────────────────────────────────────
    EMBEDDING_PROVIDER: str = "google"   # "google" | "local"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        case_sensitive=False,
        extra="ignore",
    )

    def validar_producao(self) -> list[str]:
        """
        Retorna lista de problemas de configuração em produção.
        Chamado no startup do main.py.
        """
        erros = []
        if not self.ADMIN_PASSWORD:
            erros.append("ADMIN_PASSWORD não configurada — portal admin inseguro!")
        if not self.ADMIN_CONFIRMATION_TOKEN:
            erros.append("ADMIN_CONFIRMATION_TOKEN não configurada — double-check desativado!")
        if not self.ADMIN_NUMBERS:
            erros.append("ADMIN_NUMBERS não configurada — admin via WhatsApp desativado!")
        if not self.GEMINI_API_KEY:
            erros.append("GEMINI_API_KEY não configurada — bot não funcionará!")
        return erros


settings = Settings()