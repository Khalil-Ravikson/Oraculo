"""
src/capabilities/persistence/dev_dump.py
===========================================
Capability "burra" de rascunho/teste: grava um payload como JSON em
`dados/tmp/{subdir}/{session_id}_{timestamp}.json` em vez de tocar o
Postgres. Usada enquanto `settings.DEV_TEST_NO_DB_WRITE` estiver ativo
(cadastro, funil de tickets e CRUD de teste) — ver notas_regras_negocio_chunkviz.md.

Bloqueio temporário: quando a rodada de testes acabar, os chamadores voltam a
escrever de verdade bastando desligar a flag — nenhum código de gravação real
foi removido, só guardado atrás do `if settings.DEV_TEST_NO_DB_WRITE`.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from src.infrastructure.paths import DATA_DIR

_TMP_DIR = DATA_DIR / "tmp"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_\-]")


def salvar_json_dev(subdir: str, session_id: str, payload: dict) -> str:
    """Grava `payload` em `dados/tmp/{subdir}/{session_id}_{timestamp}.json`.
    Retorna o caminho absoluto do arquivo gravado (para logging/inspeção)."""
    destino = _TMP_DIR / subdir
    destino.mkdir(parents=True, exist_ok=True)

    session_slug = _SAFE_ID.sub("_", session_id) or "sem_sessao"
    caminho = destino / f"{session_slug}_{int(time.time())}.json"
    caminho.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(caminho)
