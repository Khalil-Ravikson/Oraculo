"""
infrastructure/settings.py
===========================================================
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.infrastructure.paths import ENV_FILE


class Settings(BaseSettings):
    # ── Ambiente ──────────────────────────────────────────────────
    DEV_MODE:      bool = False
    LOG_LEVEL:     str  = "INFO"

    # Bloqueio temporário e reversível de escrita real no Postgres durante
    # testes de ponta a ponta de cadastro/tickets/CRUD via WhatsApp — ver
    # notas_regras_negocio_chunkviz.md. Enquanto True, os caminhos gateados
    # gravam em JSON local (dados/tmp/...) em vez de fazer INSERT/UPDATE real.
    # Default True enquanto durar a rodada de testes; religar via .env.
    DEV_TEST_NO_DB_WRITE: bool = True

    # Segunda flag da mesma rodada de testes: libera QUALQUER remetente a usar
    # a IA/ticket/CRUD sem precisar concluir o funil de cadastro (que hoje só
    # "conta" como registrado se cair no Postgres de verdade — coisa que
    # DEV_TEST_NO_DB_WRITE bloqueia, criando um loop sem saída no funil).
    # Também opt-in via .env, default False (produção normal exige cadastro).
    DEV_TEST_SKIP_REGISTRATION: bool = False

    # ── Banco de Dados ────────────────────────────────────────────
    DATABASE_URL:  str  
    REDIS_URL:     str  = "redis://redis:6379/0"

    # ── LLM ──────────────────────────────────────────────────────
    GEMINI_API_KEY:    str   = ""
    GEMINI_MODEL:      str   = "gemini-2.5-flash"
    GEMINI_TEMP:       float = 0.2
    GEMINI_MAX_TOKENS: int   = 1024

    # ── Multimodal (STT/TTS/Vision) ─────────────────────────────────
    # Seleção de provider por capability — ver src/infrastructure/adapters/
    # {stt,tts}_factory.py. Trocar aqui não exige mudança de código.
    STT_PROVIDER: str  = "gemini"   # opções: gemini
    TTS_PROVIDER: str  = "kokoro"   # opções: kokoro, gtts
    KOKORO_VOICE: str  = "pm_alex"  # opções: pf_dora (fem.), pm_alex/pm_santa (masc.)

    # ── Multi-provider de texto (ver infrastructure/adapters/llm_factory.py) ─
    # Provider ativo por padrão — trocável em runtime via Redis
    # (`admin:llm_provider`, sem restart) ou override por agente no
    # catálogo (`agentes_catalogo.llm_provider`, migration 007).
    LLM_PROVIDER: str = "gemini"  # "gemini" | "deepseek" | "groq"

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL:   str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    GROQ_API_KEY: str = ""
    GROQ_MODEL:   str = "llama-3.3-70b-versatile"
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    # Nightly Memory flag
    ENABLE_NIGHTLY_MEMORY: bool = False
    
    # Workers config
    RAG_SEARCH_TIMEOUT_S:  float = 10.0
    SYNTHESIS_TIMEOUT_S:   float = 12.0
    COGNITIVE_OS_TIMEOUT_S: float = 15.0

    # ── WhatsApp ──────────────────────────────────────────────────
    EVOLUTION_BASE_URL:      str = ""
    EVOLUTION_API_KEY:       str = ""
    EVOLUTION_INSTANCE_NAME: str = ""
    WHATSAPP_HOOK_URL:       str = ""

    # ── mcp_lab (laboratório de estudo MCP, ver mcp_lab/ARQUITETURA.md) ──
    BRAVE_API_KEY:  str = ""
    GITHUB_API_KEY: str = ""

    # ── RAG ───────────────────────────────────────────────────────
    PDF_PARSER:           str = "pymupdf"
    LLAMA_CLOUD_API_KEY:  str = ""
    HF_TOKEN:             str = ""
    DATA_DIR:             str = "/app/dados"
    MAX_HISTORY_MESSAGES: int = 20

    # Docling é o parser padrão pra PDF/DOCX em ParserFactory.auto(), mas
    # carrega modelos ML pesados no pre-load do worker — causa real de
    # SIGKILL sob pressão de memória (notas.md §8.5/8.6). Não havia
    # nenhuma forma de desativar sem desinstalar o pacote manualmente
    # antes desta flag existir.
    DISABLE_DOCLING: bool = False

    # ── Admin ─────────────────────────────────────────────────────
    ADMIN_USERNAME:            str = "admin"
    ADMIN_PASSWORD:            str = ""
    ADMIN_JWT_SECRET:          str = ""
    ADMIN_API_KEY:             str = ""
    ADMIN_NUMBERS:             str = ""
    ADMIN_CONFIRMATION_TOKEN:  str = ""
    STUDENT_NUMBERS:           str = ""
    ALLOWED_GROUP_ID: str = "120363409704662108@g.us"
    # Fallback quando não há override em `admin:usd_brl_rate` (Redis, editável
    # via /hub/llm-custo) — cotação aproximada, não uma fonte de câmbio ao
    # vivo (decisão deliberada: taxa fixa configurável, sem API externa).
    USD_BRL_RATE: float = 5.40

    # ── Tracing (OpenTelemetry → Jaeger, profile "monitoring") ─────
    # Desativado por padrão: só faz sentido com o container `jaeger` no ar
    # (`docker compose --profile monitoring up`). Setup nunca é crítico —
    # se o endpoint estiver inalcançável, o SDK do OTel só falha silenciosamente
    # no exporter (mesmo espírito de toda telemetria deste projeto).
    ENABLE_TRACING: bool = False
    OTEL_EXPORTER_ENDPOINT: str = "http://jaeger:4317"
    # ── Embedding ─────────────────────────────────────────────────
    EMBEDDING_PROVIDER: str = "google"
    ENV: str = "production"
    
    @property
    def is_dev(self) -> bool:
        return self.ENV.lower() == "dev"
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
        return erros


settings = Settings()