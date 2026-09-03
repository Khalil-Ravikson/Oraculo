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

    # Model tiering (ADR 0008): passos baratos e de alto volume —
    # classificação de rota (Supervisor L5), transformação de query, resumo/
    # extração de memória, parsing estruturado — usam este modelo em vez do
    # `GEMINI_MODEL` "forte". Vazio = usa o mesmo `GEMINI_MODEL` (zero mudança
    # até o operador definir, ex.: `gemini-2.5-flash-lite`). Só vale quando o
    # provider ativo é o Gemini. A síntese da resposta ao aluno SEMPRE usa o
    # modelo forte. Ver `llm_factory.get_llm_provider(rapido=True)`.
    LLM_MODEL_FAST:    str   = ""

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

    # ── Circuit breaker por provider LLM (Plano A / Fase 3, §O) ──────────────
    # Abre depois de N falhas na janela; alerta (não troca de provider); volta
    # a testar depois do cooldown. Parâmetros de partida — a prática de produção
    # convergiu em ~5 falhas / ~60s.
    LLM_CB_FALHAS_ABRE: int = 5
    LLM_CB_COOLDOWN_S:  int = 60
    LLM_CB_JANELA_S:    int = 120

    # Nightly Memory flag
    ENABLE_NIGHTLY_MEMORY: bool = False

    # ── Flags do plano de integração LangGraph/REST/MCP (Fase 1) ────
    # Todas desligadas por padrão (Decisão 00). Cada uma só passa a ser
    # consumida pelo código quando a fase correspondente do plano é
    # implementada — até lá, existem aqui só como scaffolding.
    #
    # Fase 2b: rag_node/responder_rag_direto despacham RAG/síntese pros
    # workers Celery especializados (rag_search/synthesis) em vez de
    # chamar os serviços in-process.
    FEATURE_LANGGRAPH_CELERY_DISPATCH: bool = False
    # Hub v2 Sprint 8: GraphExecutor executa uma topologia de graph_studio
    # ligada ao pipeline real. Nada lê no hot path ainda — a flag existe
    # para o dia em que um trecho piloto (busca/embeddings) for conectado.
    FEATURE_GRAPH_EXECUTOR_PILOTO: bool = False
    # Fase 3: rest_lab passa a rodar atrás de uma camada de Application
    # própria em vez de continuar como lab isolado.
    FEATURE_REST_PRODUCT: bool = False
    # Fase 4: mcp_lab passa a rodar atrás de uma camada de Application
    # própria, sem acesso direto a adapters de produção (EvolutionAdapter).
    FEATURE_MCP_PRODUCT: bool = False

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
    # Defaults das chaves reconectadas na Fase 1 (Dynamic Configuration).
    # Estes valores são o fallback hardcoded de `dynamic_config.get_*` quando
    # Redis/Postgres estão indisponíveis — mesma filosofia de `pricing._PRECOS`.
    # O valor efetivo em runtime vem de `config_dinamica` (editável via
    # /hub/config, sem restart).
    RAG_CACHE_TTL_SECONDS: int  = 3600
    RAG_RERANKER_ENABLED:  bool = True
    # Fase 4: prioridade/enable de parser via config dinâmica (/hub/config).
    PARSER_PDF_PRIORIDADE: str  = "docling,pymupdf"
    PARSER_DESABILITADOS:  str  = ""
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
    # ADR 0008 Fase 2: pra onde o nó `human_handoff` manda o aviso de que uma
    # conversa precisa de atendente humano. Vazio → cai no 1º de ADMIN_NUMBERS.
    SUPPORT_GROUP_JID: str = ""
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