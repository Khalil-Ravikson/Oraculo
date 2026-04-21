"""
infrastructure/logging_config.py — Configuração de Logging para Docker
=======================================================================

PROBLEMA RESOLVIDO:
  No Docker, o Python usa buffer em modo não-interativo.
  Por padrão o stdout é bufferizado em blocos de 4KB — logs aparecem
  em rajadas ou ficam presos no buffer e nunca chegam ao `docker logs`.

  A solução: PYTHONUNBUFFERED=1 no Dockerfile (já está lá) +
  StreamHandler sem buffer + formatação com timestamp completo.

COMO USAR (em src/main.py):
    from src.infrastructure.logging_config import setup_logging
    setup_logging(level="INFO")

MODO DEV vs PROD:
  DEV:  formato legível com cores (nível com emoji)
  PROD: formato JSON estruturado para ingestão em ELK/Loki/Grafana
"""
from __future__ import annotations

import logging
import sys


_NIVEL_EMOJI = {
    "DEBUG":    "🔵",
    "INFO":     "✅",
    "WARNING":  "⚠️ ",
    "ERROR":    "❌",
    "CRITICAL": "🚨",
}


class _ColourFormatter(logging.Formatter):
    """Formatter simples com emoji e timestamp — bom para docker logs."""

    FORMAT = "%(asctime)s %(emoji)s %(name)-22s | %(message)s"
    DATEFMT = "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        record.emoji = _NIVEL_EMOJI.get(record.levelname, "  ")
        return super().format(record)


def setup_logging(level: str = "INFO") -> None:
    """
    Configura logging para toda a aplicação.

    REGRAS:
      - Root logger captura tudo.
      - Saída vai para stdout SEM buffer (importante para Docker).
      - Loggers de bibliotecas ruidosas são silenciados para WARNING.
      - O nível da aplicação (src.*) usa o nível passado.

    Chamado UMA vez em startup() do FastAPI e em cada Celery worker.
    """
    # Stream sem buffer — força flush imediato no Docker
    handler = logging.StreamHandler(sys.stdout)
    handler.stream.reconfigure(line_buffering=True)  # Python 3.7+
    handler.setFormatter(_ColourFormatter(
        fmt=_ColourFormatter.FORMAT,
        datefmt=_ColourFormatter.DATEFMT,
    ))

    nivel = getattr(logging, level.upper(), logging.INFO)

    # Root logger
    root = logging.getLogger()
    root.setLevel(nivel)
    # Remove handlers duplicados em re-configurações
    root.handlers.clear()
    root.addHandler(handler)

    # Silencia bibliotecas que poluem o log
    _QUIET = [
        "httpx", "httpcore", "hpack",
        "celery.utils.functional", "celery.app.trace",
        "redis", "redis.asyncio",
        "urllib3", "asyncio",
        "langchain_core.callbacks",
        "langchain.callbacks",
        "google.auth",
        "PIL",
    ]
    for lib in _QUIET:
        logging.getLogger(lib).setLevel(logging.WARNING)

    # Nossa aplicação sempre usa o nível configurado
    logging.getLogger("src").setLevel(nivel)

    logging.getLogger(__name__).info(
        "📋 Logging configurado | nível=%s | stdout sem buffer", level.upper()
    )