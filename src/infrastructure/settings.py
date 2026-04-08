"""
infrastructure/settings.py — Sprint 1 (Langfuse variables)
===========================================================

ADICIONADO:
  LANGFUSE_SECRET_KEY  → chave secreta gerada no painel Langfuse
  LANGFUSE_PUBLIC_KEY  → chave pública para o SDK
  LANGFUSE_HOST        → URL do container Langfuse (interno Docker)

Se as chaves estiverem vazias, o tracing é desativado silenciosamente.
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

    # ── Admin ─────────────────────────────────────────────────────
    ADMIN_USERNAME:            str = "admin"
    ADMIN_PASSWORD:            str = ""
    ADMIN_JWT_SECRET:          str = ""
    ADMIN_API_KEY:             str = ""
    ADMIN_NUMBERS:             str = ""
    ADMIN_CONFIRMATION_TOKEN:  str = ""
    STUDENT_NUMBERS:           str = ""

    # ── Embedding ─────────────────────────────────────────────────
    EMBEDDING_PROVIDER: str = "google"

    # ── Sprint 1: Langfuse (Observabilidade LLM) ──────────────────
    # Gere as chaves em http://localhost:3000 → Settings → API Keys
    # Se vazias, o tracing é desativado silenciosamente (sem erro).
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_HOST:       str = "http://langfuse:3000"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        case_sensitive=False,
        extra="ignore",
    )

    def validar_producao(self) -> list[str]:
        erros = []
        if not self.ADMIN_PASSWORD:
            erros.append("ADMIN_PASSWORD não configurada — portal admin inseguro!")
        if not self.ADMIN_CONFIRMATION_TOKEN:
            erros.append("ADMIN_CONFIRMATION_TOKEN não configurada!")
        if not self.ADMIN_NUMBERS:
            erros.append("ADMIN_NUMBERS não configurada!")
        if not self.GEMINI_API_KEY:
            erros.append("GEMINI_API_KEY não configurada — bot não funcionará!")
        # Sprint 1: aviso sobre Langfuse (não é crítico)
        if not self.LANGFUSE_SECRET_KEY:
            erros.append("LANGFUSE_SECRET_KEY vazia — tracing LLM desativado.")
        return erros


settings = Settings()