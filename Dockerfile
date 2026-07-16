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
RUN --mount=type=cache,target=/root/.cache/uv \
    /uv/bin/uv venv /opt/venv && \
    /uv/bin/uv pip install -r requirements.txt

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

# Cópia ordenada por frequência de alteração (mais frequente por último)
COPY --chown=oraculo:oraculo alembic.ini ./
COPY --chown=oraculo:oraculo migrations/ ./migrations/
COPY --chown=oraculo:oraculo dados/      ./dados/
COPY --chown=oraculo:oraculo templates/  ./templates/
COPY --chown=oraculo:oraculo static/     ./static/
COPY --chown=oraculo:oraculo src/        ./src/

USER oraculo
RUN python -c "import os; os.environ['HF_HOME']='/home/oraculo/.cache/huggingface'; from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512)"

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1", "--loop", "uvloop", "--no-access-log"]