"""
Plano A / Fase 1 — `src/infrastructure/dynamic_config.py`.

Cobre o contrato da camada síncrona (caminho quente): precedência
Redis → default, degradação sem exceção, coerção de tipo, e a validação
`normalizar_para_persistir` usada pelo endpoint admin.

Os testes de concorrência / read-repair / histórico contra Postgres real
ficam em `test_dynamic_config_repository.py`.
"""
import importlib.util
import pathlib

import pytest

from src.infrastructure import dynamic_config as dc
from src.infrastructure.settings import settings

def _load_seed(nome: str):
    p = pathlib.Path(__file__).parents[3] / "migrations" / "versions" / nome
    spec = importlib.util.spec_from_file_location(nome.replace(".py", ""), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod._SEED)


# Seed acumulado das migrations que alimentam config_dinamica, menos as chaves
# que migrations posteriores removem (ADR 0008 Fase 3 apaga
# FEATURE_LANGGRAPH_NATIVE_ROUTES).
_REMOVIDAS = {"FEATURE_LANGGRAPH_NATIVE_ROUTES"}
SEED_009 = _load_seed("009_config_dinamica.py")
SEED_TODAS = [
    row for row in (
        SEED_009
        + _load_seed("011_config_parser.py")
        + _load_seed("020_config_graph_executor.py")
    )
    if row[0] not in _REMOVIDAS
]


class _FakeRedisText:
    def __init__(self, dados=None):
        self._d = dict(dados or {})

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v):
        self._d[k] = str(v)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedisText()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: fake)
    return fake


# ─── Precedência Redis → default ─────────────────────────────────────────────

def test_get_str_le_do_redis(fake_redis):
    fake_redis.set("config:GEMINI_MODEL", "gemini-2.5-pro")
    assert dc.get_str("GEMINI_MODEL") == "gemini-2.5-pro"


def test_get_str_cai_no_default_settings_quando_redis_vazio(fake_redis):
    assert dc.get_str("GEMINI_MODEL") == settings.GEMINI_MODEL


def test_get_int_le_do_redis_e_tipa(fake_redis):
    fake_redis.set("config:RAG_CACHE_TTL_SECONDS", "7200")
    valor = dc.get_int("RAG_CACHE_TTL_SECONDS")
    assert valor == 7200 and isinstance(valor, int)


def test_get_int_default_quando_vazio(fake_redis):
    assert dc.get_int("RAG_CACHE_TTL_SECONDS") == settings.RAG_CACHE_TTL_SECONDS


@pytest.mark.parametrize("bruto,esperado", [
    ("true", True), ("1", True), ("yes", True), ("on", True), ("sim", True),
    ("false", False), ("0", False), ("off", False), ("qualquer-lixo", False),
])
def test_get_bool_coerce(fake_redis, bruto, esperado):
    fake_redis.set("config:RAG_RERANKER_ENABLED", bruto)
    assert dc.get_bool("RAG_RERANKER_ENABLED") is esperado


def test_get_bool_default_quando_vazio(fake_redis):
    assert dc.get_bool("RAG_RERANKER_ENABLED") is settings.RAG_RERANKER_ENABLED


# ─── Degradação sem exceção (§T) ────────────────────────────────────────────

def test_get_str_com_redis_fora_do_ar_cai_no_default(monkeypatch):
    def _boom():
        raise ConnectionError("Redis indisponível (simulado)")
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", _boom)

    # Não levanta — cai no default hardcoded.
    assert dc.get_str("GEMINI_MODEL") == settings.GEMINI_MODEL
    assert dc.get_bool("RAG_RERANKER_ENABLED") is settings.RAG_RERANKER_ENABLED


def test_get_int_com_valor_corrompido_no_redis_cai_no_default(fake_redis):
    fake_redis.set("config:RAG_CACHE_TTL_SECONDS", "não-é-número")
    assert dc.get_int("RAG_CACHE_TTL_SECONDS") == settings.RAG_CACHE_TTL_SECONDS


# ─── Validação para persistir (endpoint admin) ──────────────────────────────

def test_normalizar_bool_aceita_sinonimos():
    assert dc.normalizar_para_persistir("RAG_RERANKER_ENABLED", "yes") == ("true", "bool")
    assert dc.normalizar_para_persistir("RAG_RERANKER_ENABLED", "0") == ("false", "bool")


def test_normalizar_int_valida_e_canoniza():
    assert dc.normalizar_para_persistir("RAG_CACHE_TTL_SECONDS", " 900 ") == ("900", "int")


def test_normalizar_rejeita_ttl_nao_positivo():
    # TTL 0/negativo faria o Redis apagar a entrada na hora — cache desligado sem aviso.
    for ruim in ("0", "-1", "-3600"):
        with pytest.raises(dc.ValorInvalido):
            dc.normalizar_para_persistir("RAG_CACHE_TTL_SECONDS", ruim)


def test_normalizar_str_rejeita_vazio():
    with pytest.raises(dc.ValorInvalido):
        dc.normalizar_para_persistir("GEMINI_MODEL", "   ")


def test_normalizar_int_rejeita_nao_numero():
    with pytest.raises(dc.ValorInvalido):
        dc.normalizar_para_persistir("RAG_CACHE_TTL_SECONDS", "abc")


def test_normalizar_bool_rejeita_valor_ambiguo():
    with pytest.raises(dc.ValorInvalido):
        dc.normalizar_para_persistir("RAG_RERANKER_ENABLED", "talvez")


def test_normalizar_rejeita_chave_fora_da_allowlist():
    with pytest.raises(dc.ChaveNaoPermitida):
        dc.normalizar_para_persistir("DATABASE_URL", "postgres://evil")


def test_snapshot_marca_chaves_nao_reconectadas():
    snap = {c["chave"]: c for c in dc.snapshot([])}
    assert snap["GEMINI_MODEL"]["reconectada"] is True
    assert snap["RAG_RERANKER_ENABLED"]["reconectada"] is True
    assert snap["DEV_TEST_NO_DB_WRITE"]["reconectada"] is False
    # chaves não gravadas: versao 0 + valor = default
    assert snap["GEMINI_MODEL"]["versao"] == 0
    assert snap["GEMINI_MODEL"]["valor"] == snap["GEMINI_MODEL"]["default"]


def test_allowlist_defaults_e_seed_da_migration_batem():
    """Trava contra divergência silenciosa entre as 3 fontes: o seed da
    migration 009, os tipos de `ALLOWED_DYNAMIC_KEYS` e os defaults *de código*
    de `settings.py` (o default do campo pydantic — não a instância `settings`,
    que em runtime reflete o `.env` e pode legitimamente divergir)."""
    seed = {chave: (tipo, valor) for chave, tipo, valor in SEED_TODAS}

    assert set(seed) == set(dc.ALLOWED_DYNAMIC_KEYS)
    for chave, (tipo, valor) in seed.items():
        assert dc.ALLOWED_DYNAMIC_KEYS[chave] == tipo, chave
        default_codigo = settings.__class__.model_fields[chave].default
        assert dc._canonico(default_codigo) == valor, chave
