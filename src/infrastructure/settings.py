# src/infrastructure/settings.py — VERSÃO ÚNICA
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.infrastructure.paths import ENV_FILE

class Settings(BaseSettings):
    # ── Ambiente ──────────────────────────────────────────────────
    DEV_MODE:      bool = False
    LOG_LEVEL:     str  = "INFO"
    ADMIN_API_KEY: str  = ""

    # ── Banco de Dados ────────────────────────────────────────────
    DATABASE_URL:  str  = "postgresql+asyncpg://user:pass@localhost/oraculo"
    REDIS_URL:     str  = "redis://redis:6379/0"

    # ── LLM ──────────────────────────────────────────────────────
    GEMINI_API_KEY:    str   = ""
    GEMINI_MODEL:      str   = "gemini-2.0-flash-lite"
    GEMINI_TEMP:       float = 0.2
    GEMINI_MAX_TOKENS: int   = 1024

    # ── WhatsApp ──────────────────────────────────────────────────
    EVOLUTION_BASE_URL:     str = ""
    EVOLUTION_API_KEY:      str = ""
    EVOLUTION_INSTANCE_NAME:str = ""
    WHATSAPP_HOOK_URL:      str = ""

    # ── RAG ───────────────────────────────────────────────────────
    PDF_PARSER:            str = "pymupdf"
    LLAMA_CLOUD_API_KEY:   str = ""
    HF_TOKEN:              str = ""
    DATA_DIR:              str = "/app/dados"
    MAX_HISTORY_MESSAGES:  int = 20

    # ── RBAC ──────────────────────────────────────────────────────
    ADMIN_NUMBERS:   str = ""  # "5598999990001,5598999990002"
    STUDENT_NUMBERS: str = ""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        case_sensitive=False,
        extra="ignore",
    )

settings = Settings()