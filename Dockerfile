# =============================================================================
# Dockerfile — Oráculo UEMA (Multi-stage, BuildKit Cache, UV, Rootless)
# =============================================================================

# Define a variável de ambiente global para o caminho do Playwright
ARG PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
ARG PLAYWRIGHT_BROWSERS_PATH

ENV PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}
ENV PATH="/opt/venv/bin:$PATH"

# Instala uv para builds ultra rápidos
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv

WORKDIR /build

COPY requirements.txt .

# Cria o virtualenv e instala dependências via uv com cache de downloads
# --index-strategy unsafe-best-match: necessário por causa do --extra-index-url
# do torch (CPU-only) no requirements.txt. Sem essa flag, o uv trava na
# resolução de `requests` a partir do índice do PyTorch (só tem 2.28.1),
# em conflito com a exigência >=2.31 do llama-parse/llama-index-core.
RUN --mount=type=cache,target=/root/.cache/uv \
    /uv/bin/uv venv /opt/venv && \
    /uv/bin/uv pip install --index-strategy unsafe-best-match -r requirements.txt

# Instala o Chromium direto no diretório definitivo sem cópias redundantes
RUN /opt/venv/bin/playwright install chromium

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
ARG PLAYWRIGHT_BROWSERS_PATH

# Garante que o runtime conheça o caminho do navegador e do virtualenv
ENV PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/app"

# Cria utilizador sem privilégios
RUN groupadd --gid 1001 oraculo && \
    useradd  --uid 1001 --gid oraculo --shell /bin/bash --create-home oraculo

WORKDIR /app

# Copia o venv e os binários do Chromium do builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder ${PLAYWRIGHT_BROWSERS_PATH} ${PLAYWRIGHT_BROWSERS_PATH}

# Instala bibliotecas nativas essenciais e as dependências nativas do Chromium
# Agora que o virtualenv está copiado, a instalação automatizada funciona 100%
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && /opt/venv/bin/playwright install-deps chromium \
    && apt-get purge -y --auto-remove \
    && rm -rf /var/lib/apt/lists/*

USER oraculo

# Download do modelo ANTES de copiar o código-fonte: depende só do venv/apt,
# não de src/. Assim uma mudança em src/ não invalida esta camada e não
# força re-download do HuggingFace a cada build.
RUN python -c "import os; os.environ['HF_HOME']='/home/oraculo/.cache/huggingface'; from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512)"

# Kokoro-82M (TTS pt-BR) — mesma lógica: baixa o modelo base + a voz padrão
# (pf_dora) em build-time, cacheado no mesmo HF_HOME. Uma síntese real (não
# só instanciar o pipeline) força o download da voz também, não só do modelo
# base — testado localmente antes de entrar aqui (ver notas.md seção 11).
RUN python -c "import os; os.environ['HF_HOME']='/home/oraculo/.cache/huggingface'; from kokoro import KPipeline; p = KPipeline(lang_code='p'); list(p('teste', voice='pf_dora'))"

# Cópia ordenada por frequência de alteração (mais frequente por último)
COPY --chown=oraculo:oraculo alembic.ini ./
COPY --chown=oraculo:oraculo migrations/ ./migrations/
COPY --chown=oraculo:oraculo dados/      ./dados/
COPY --chown=oraculo:oraculo templates/  ./templates/
COPY --chown=oraculo:oraculo static/     ./static/
COPY --chown=oraculo:oraculo src/        ./src/
# Decisão 06 do plano de integração LangGraph/REST/MCP (Fase 6, 2026-08-25):
# até aqui só existiam via bind-mount em docker-compose.yml — uma imagem
# construída/publicada sem esse mount (deploy real fora do compose de dev)
# quebrava com ImportError na primeira mensagem "rest ..."/"stack ...".
COPY --chown=oraculo:oraculo rest_lab/    ./rest_lab/
COPY --chown=oraculo:oraculo mcp_lab/     ./mcp_lab/

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1", "--loop", "uvloop", "--no-access-log"]