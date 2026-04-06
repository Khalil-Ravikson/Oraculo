import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Importa a tua aplicação FastAPI principal
from src.main import app 

# Importa as configurações do Celery
from src.infrastructure.celery_app import celery_app

@pytest.fixture(scope="session", autouse=True)
def configurar_celery_para_testes():
    """
    Força o Celery a rodar de forma síncrona durante os testes.
    Isso significa que quando o webhook chamar `task.delay()`, 
    o código vai executar na mesma hora, permitindo testar o fluxo completo.
    """
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True  # Exceções no Celery sobem para o pytest
    )
    yield

@pytest.fixture
def cliente_api():
    """Fornece um cliente HTTP de testes para o FastAPI."""
    with TestClient(app) as client:
        yield client

@pytest.fixture
def mock_evolution_gateway():
    """
    Interceta as chamadas da Evolution API. 
    Como estás a usar Clean Arch, substituímos a saída no Adapter.
    Ajusta o path do patch conforme a tua estrutura exata.
    """
    with patch("src.infrastructure.adapters.evolution_adapter.EvolutionAdapter.enviar_mensagem_texto") as mock_send:
        yield mock_send