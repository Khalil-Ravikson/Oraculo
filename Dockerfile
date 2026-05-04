# =============================================================================
# Dockerfile — Oráculo UEMA (Multi-stage, rootless, produção)
# =============================================================================

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# No builder, precisamos apenas do essencial para compilar os pacotes Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 🔥 AQUI ESTÁ A CORREÇÃO: Usando cache do BuildKit e timeout estendido 🔥
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip

RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --default-timeout=120 -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# 🚨 AQUI É O LUGAR CORRETO: As bibliotecas do OpenCV precisam estar no Runtime!
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libxcb1 \
    libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Cria utilizador sem privilégios
RUN groupadd --gid 1001 oraculo && \
    useradd  --uid 1001 --gid oraculo --shell /bin/bash --create-home oraculo

WORKDIR /app

# Copia o venv do builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/app"

# Copia código da aplicação
COPY --chown=oraculo:oraculo src/    ./src/
COPY --chown=oraculo:oraculo dados/  ./dados/
COPY --chown=oraculo:oraculo templates/ ./templates/
COPY --chown=oraculo:oraculo static/    ./static/
COPY --chown=oraculo:oraculo alembic.ini ./
COPY --chown=oraculo:oraculo migrations/ ./migrations/

USER oraculo
EXPOSE 9000

# Healthcheck interno
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "9000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--no-access-log"]