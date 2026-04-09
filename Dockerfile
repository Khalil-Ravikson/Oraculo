# =============================================================================
# Dockerfile — Oráculo UEMA (Multi-stage, rootless, produção)
# =============================================================================

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Dependências de sistema apenas para compilação
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala dependências em venv isolado
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip --no-cache-dir && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Copia apenas o runtime das libs de sistema (não os headers de compilação)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
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
# COPY --chown=oraculo:oraculo alembic/    ./alembic/

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