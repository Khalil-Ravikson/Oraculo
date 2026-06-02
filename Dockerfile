# =============================================================================
# Dockerfile — Oráculo UEMA (Multi-stage, BuildKit Cache, Rootless)
# =============================================================================

# Define a variável de ambiente global para o caminho do Playwright
ARG PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
ARG PLAYWRIGHT_BROWSERS_PATH

# Exporta a variável para as instruções do Builder
ENV PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}

WORKDIR /build

# Cache do APT para o gerenciador de pacotes do Linux
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends curl

COPY requirements.txt .

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip

# Cache do pip
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --default-timeout=120 -r requirements.txt

# Agora o build vai passar direto por aqui usando o cache perfeitamente!
RUN --mount=type=cache,target=/root/.cache/ms-playwright \
    /opt/venv/bin/playwright install chromium && \
    mkdir -p ${PLAYWRIGHT_BROWSERS_PATH} && \
    cp -r /root/.cache/ms-playwright/. ${PLAYWRIGHT_BROWSERS_PATH}/

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
ARG PLAYWRIGHT_BROWSERS_PATH

# Garante que o runtime conheça o caminho do navegador
ENV PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}

# 🔥 OTIMIZAÇÃO 3: Cache do APT no Runtime + Instalação de dependências do sistema do Playwright
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libxcb1 \
    libx11-xcb1 \
    # Caso falte alguma dependência do chromium, o comando abaixo resolve em build time:
    && /opt/venv/bin/playwright install-deps chromium 2>/dev/null || true

# Cria utilizador sem privilégios
RUN groupadd --gid 1001 oraculo && \
    useradd  --uid 1001 --gid oraculo --shell /bin/bash --create-home oraculo

WORKDIR /app

# Copia o venv do builder
COPY --from=builder /opt/venv /opt/venv

# 🔥 CORREÇÃO CRUCIAL: Copia os binários do Chromium que foram gerados no builder
COPY --from=builder ${PLAYWRIGHT_BROWSERS_PATH} ${PLAYWRIGHT_BROWSERS_PATH}

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/app"

# 🔥 OTIMIZAÇÃO 4: Ordem de COPY por frequência de alteração (Menos frequentes primeiro)
COPY --chown=oraculo:oraculo alembic.ini ./
COPY --chown=oraculo:oraculo migrations/ ./migrations/
COPY --chown=oraculo:oraculo dados/      ./dados/
COPY --chown=oraculo:oraculo templates/  ./templates/
COPY --chown=oraculo:oraculo static/     ./static/
# O código fonte (src) muda toda hora, fica por último para não quebrar o cache de cima
COPY --chown=oraculo:oraculo src/        ./src/

USER oraculo
EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1", "--loop", "uvloop", "--no-access-log"]