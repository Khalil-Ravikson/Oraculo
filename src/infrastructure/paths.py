# src/infrastructure/paths.py
from pathlib import Path

# __file__ aponta para src/infrastructure/paths.py
# .parent (infrastructure) -> .parent (src) -> .parent (raiz: Oraculo-main)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Diretórios Principais
SRC_DIR       = PROJECT_ROOT / "src"
DATA_DIR      = PROJECT_ROOT / "dados"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR    = PROJECT_ROOT / "static"

# Arquivos Específicos
ENV_FILE      = PROJECT_ROOT / ".env"

# Criar diretórios essenciais se não existirem
# O Docker já deve ter criado a pasta 'dados' via volume, mas garantimos aqui:
try:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "uploads").mkdir(exist_ok=True)
except PermissionError:
    # Ignora erro de permissão caso o container não seja root
    pass

# REMOVIDO: LOGS_DIR.mkdir(exist_ok=True) para evitar PermissionError no Docker