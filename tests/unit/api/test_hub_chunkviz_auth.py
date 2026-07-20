# tests/unit/api/test_hub_chunkviz_auth.py
"""
Sprint 3 (Fase 1) — os 6 endpoints de ChunkViz chamavam `_verificar_cookie(request)`
como statement solto, sem checar o retorno (a função nunca lança exceção, só
retorna `TokenPayload | None`). Resultado: qualquer requisição anônima
conseguia disparar upload/chunking/ingestão real no RAG sem autenticação.

Estes testes montam só o router do hub (sem subir a app inteira / lifespan,
que exigiria Postgres/Redis reais) e confirmam 401 para requisição anônima.
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.web.hub import router
from src.application.use_cases.admin_auth import TokenPayload


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_cv_upload_sem_cookie_retorna_401(client):
    r = client.post("/hub/chunkviz/upload", files={"file": ("a.txt", b"hello")})
    assert r.status_code == 401


def test_cv_get_page_sem_cookie_retorna_401(client):
    r = client.post("/hub/chunkviz/page", data={"file_id": "abc", "page": 0})
    assert r.status_code == 401


def test_cv_simulate_sem_cookie_retorna_401(client):
    r = client.post("/hub/chunkviz/simulate", json={"text": "algum texto"})
    assert r.status_code == 401


def test_cv_ingest_sem_cookie_retorna_401(client):
    r = client.post("/hub/chunkviz/ingest", json={"file_id": "abc"})
    assert r.status_code == 401


def test_cv_task_status_sem_cookie_retorna_401(client):
    r = client.get("/hub/chunkviz/task/some-task-id")
    assert r.status_code == 401


def test_cv_extract_url_sem_cookie_retorna_401(client):
    r = client.post("/hub/chunkviz/extract-url", data={"url": "https://example.com"})
    assert r.status_code == 401


def test_cv_task_status_com_cookie_valido_segue_fluxo_normal(client):
    """Confirma que a correção não quebrou o caminho autenticado."""
    payload = TokenPayload(sub="admin", iat=int(time.time()), exp=int(time.time()) + 3600)
    mock_auth = MagicMock()
    mock_auth.token_esta_bloqueado.return_value = False
    mock_auth.verificar_token.return_value = payload

    mock_result = MagicMock(state="SUCCESS", result={"ok": True})
    with patch("src.api.routers.web.hub.get_admin_auth", return_value=mock_auth), \
         patch("src.infrastructure.celery_app.celery_app.AsyncResult", return_value=mock_result):
        client.cookies.set("admin_token", "valid-token")
        r = client.get("/hub/chunkviz/task/some-task-id")

    assert r.status_code == 200
    assert r.json()["state"] == "SUCCESS"
