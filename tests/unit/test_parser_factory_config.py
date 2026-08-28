"""
Plano A / Fase 4 — ParserFactory.auto() consome PARSER_PDF_PRIORIDADE e
PARSER_DESABILITADOS da config dinâmica (§J/§K).
"""
from unittest.mock import patch

import pytest

from src.rag.ingestion.parser_factory import ParserFactory


class _FakeRedisText:
    def __init__(self, d=None):
        self._d = dict(d or {})

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v):
        self._d[k] = str(v)


@pytest.fixture
def pedidos(monkeypatch):
    """Registra a ordem em que ParserFactory.get() é chamado e devolve um
    objeto qualquer (parser 'ok') só p/ o primeiro nome não desabilitado."""
    chamados = []

    def _get(nome):
        chamados.append(nome)
        return object()

    monkeypatch.setattr(ParserFactory, "get", staticmethod(_get))
    monkeypatch.setattr("src.rag.ingestion.parser_factory._detect_pdf_scan", lambda *a, **k: False)
    # evita o wrapper de OCR (get_rapidocr) no fim de auto() p/ PDF
    monkeypatch.setattr("src.rag.ingestion.parser_factory._get_rapidocr_parser",
                        lambda: (_ for _ in ()).throw(RuntimeError("sem ocr no teste")))
    return chamados


def _redis(dados):
    fake = _FakeRedisText(dados)
    return patch("src.infrastructure.redis_client.get_redis_text", lambda: fake)


def test_prioridade_pdf_vem_da_config(pedidos):
    with _redis({"config:PARSER_PDF_PRIORIDADE": "pymupdf,docling", "config:PARSER_DESABILITADOS": ""}):
        ParserFactory.auto("/tmp/edital.pdf")
    assert pedidos[0] == "pymupdf"


def test_default_quando_config_vazia(pedidos, monkeypatch):
    from src.infrastructure.settings import settings
    monkeypatch.setattr(settings, "DISABLE_DOCLING", False, raising=False)
    monkeypatch.setattr(settings, "PARSER_PDF_PRIORIDADE", "docling,pymupdf", raising=False)
    with _redis({}):  # Redis vazio → cai no default de settings
        ParserFactory.auto("/tmp/edital.pdf")
    assert pedidos[0] == "docling"


def test_parser_desabilitado_e_pulado(pedidos):
    with _redis({"config:PARSER_PDF_PRIORIDADE": "docling,pymupdf", "config:PARSER_DESABILITADOS": "docling"}):
        ParserFactory.auto("/tmp/edital.pdf")
    assert "docling" not in pedidos
    assert pedidos[0] == "pymupdf"


def test_disable_docling_legado_ainda_funciona(pedidos, monkeypatch):
    from src.infrastructure.settings import settings
    monkeypatch.setattr(settings, "DISABLE_DOCLING", True, raising=False)
    with _redis({"config:PARSER_PDF_PRIORIDADE": "docling,pymupdf", "config:PARSER_DESABILITADOS": ""}):
        ParserFactory.auto("/tmp/edital.pdf")
    assert "docling" not in pedidos
