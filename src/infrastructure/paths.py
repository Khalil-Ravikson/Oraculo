from pathlib import Path

# __file__ aponta para este arquivo (paths.py).
# .resolve() pega o caminho absoluto.
# .parent (infrastructure) -> .parent (src) -> .parent (oraculo - raiz)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Diretórios Principais (Regras 5 e Gerais)
SRC_DIR = PROJECT_ROOT / "src"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

# Arquivos Específicos
ENV_FILE = PROJECT_ROOT / ".env"

# Quando tivermos logs ou certificados no futuro, adicionamos aqui:
# LOGS_DIR = PROJECT_ROOT / "logs"