"""Catálogo de preços do OpenRouter.

Usado **só para consultar preço**, nunca para inferência. Os testes usam
dublês: nenhum depende do OpenRouter estar no ar, como pede a especificação.

O invariante mais importante: `preco_de()` roda no caminho de toda resposta
de LLM e **não pode fazer chamada de rede**. Quem fala com a internet é
`atualizar()`, na tarefa periódica.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.infrastructure.observability import openrouter_catalog as cat
from src.infrastructure.observability.usage import PricingSource

_PAYLOAD = {
    "data": [
        {"id": "google/gemini-2.5-flash",
         "pricing": {"prompt": "0.0000003", "completion": "0.0000025",
                     "input_cache_read": "0.00000003"}},
        {"id": "deepseek/deepseek-chat",
         "pricing": {"prompt": "0.0000002", "completion": "0.0000012"}},
        {"id": "modelo/gratuito", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "sem/precos", "pricing": {}},
        {"id": None, "pricing": {"prompt": "0.1"}},
    ]
}


# ── Normalização ─────────────────────────────────────────────────────────

def test_converte_preco_por_token_para_milhao():
    """O OpenRouter publica por token; o projeto inteiro usa por 1M."""
    assert cat._por_1m("0.0000003") == Decimal("0.30")


def test_gratuito_e_zero_nao_ausente():
    """Modelo grátis tem preço zero. Diferente de não ter preço."""
    assert cat._por_1m("0") == Decimal("0")


@pytest.mark.parametrize("valor", [None, "", "abc", "-0.5"])
def test_valores_invalidos_viram_none(valor):
    assert cat._por_1m(valor) is None


def test_normalizar_descarta_modelo_sem_preco_utilizavel():
    catalogo = cat._normalizar(_PAYLOAD)
    assert "sem/precos" not in catalogo
    assert "google/gemini-2.5-flash" in catalogo


def test_normalizar_ignora_id_invalido():
    catalogo = cat._normalizar(_PAYLOAD)
    assert None not in catalogo
    assert len(catalogo) == 3


def test_normalizar_aceita_payload_vazio():
    assert cat._normalizar({}) == {}


# ── Consulta (sem rede) ──────────────────────────────────────────────────

@pytest.fixture
def catalogo_no_cache(monkeypatch):
    envelope = {"atualizado_em": "2026-09-11T10:00:00+00:00",
                "modelos": cat._normalizar(_PAYLOAD)}
    monkeypatch.setattr(cat, "_carregar", lambda: envelope)
    return envelope


def test_encontra_pelo_nome_curto(catalogo_no_cache):
    """Aqui o modelo se chama `gemini-2.5-flash`; lá, `google/gemini-2.5-flash`."""
    preco = cat.preco_de("gemini", "gemini-2.5-flash")
    assert preco is not None
    assert preco.input_por_1m == Decimal("0.30")
    assert preco.output_por_1m == Decimal("2.50")
    assert preco.origem is PricingSource.OPENROUTER


def test_traz_o_preco_de_cache_quando_publicado(catalogo_no_cache):
    preco = cat.preco_de("gemini", "gemini-2.5-flash")
    assert preco.cache_por_1m == Decimal("0.03")


def test_ausencia_de_cache_e_none_nao_zero(catalogo_no_cache):
    """Modelo sem preço de cache publicado não é modelo com cache grátis."""
    preco = cat.preco_de("deepseek", "deepseek-chat")
    assert preco.cache_por_1m is None


def test_modelo_desconhecido_devolve_none(catalogo_no_cache):
    assert cat.preco_de("gemini", "modelo-que-nao-existe") is None


def test_cache_vazio_devolve_none(monkeypatch):
    monkeypatch.setattr(cat, "_carregar", lambda: {})
    assert cat.preco_de("gemini", "gemini-2.5-flash") is None


def test_registra_a_data_do_catalogo(catalogo_no_cache):
    """Sem a versão do preço, recalcular o histórico depois de uma
    atualização daria outro número e a auditoria não fecharia."""
    assert cat.preco_de("gemini", "gemini-2.5-flash").versao.startswith("2026-09-11")


# ── Atualização: falhas não podem quebrar nada ───────────────────────────

class _RespostaFake:
    def __init__(self, status=200, payload=None, texto=None):
        self.status_code = status
        self._payload = payload if payload is not None else _PAYLOAD
        # `content` é só o tamanho do corpo para o teto de sanidade; serializar
        # o sentinela de "JSON inválido" não faria sentido.
        corpo = texto if texto is not None else (
            "{corpo ilegivel}" if self._payload is _INVALIDO else json.dumps(self._payload)
        )
        self.content = corpo.encode()

    def json(self):
        if self._payload is _INVALIDO:
            raise ValueError("JSON inválido")
        return self._payload


_INVALIDO = object()


def _cliente_fake(resposta):
    class _Cliente:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            if isinstance(resposta, Exception):
                raise resposta
            return resposta

    return _Cliente


async def _rodar(monkeypatch, resposta):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _cliente_fake(resposta))
    gravados = {}
    monkeypatch.setattr(cat, "_carregar", lambda: gravados)
    return await cat.atualizar()


@pytest.mark.asyncio
async def test_rate_limit_mantem_o_catalogo(monkeypatch):
    assert await _rodar(monkeypatch, _RespostaFake(status=429)) == 0


@pytest.mark.asyncio
async def test_erro_http_mantem_o_catalogo(monkeypatch):
    assert await _rodar(monkeypatch, _RespostaFake(status=500)) == 0


@pytest.mark.asyncio
async def test_json_invalido_nao_levanta(monkeypatch):
    assert await _rodar(monkeypatch, _RespostaFake(payload=_INVALIDO)) == 0


@pytest.mark.asyncio
async def test_rede_fora_nao_levanta(monkeypatch):
    assert await _rodar(monkeypatch, RuntimeError("sem rede")) == 0


@pytest.mark.asyncio
async def test_payload_gigante_e_recusado(monkeypatch):
    grande = _RespostaFake(texto="x" * (9 * 1024 * 1024))
    assert await _rodar(monkeypatch, grande) == 0
