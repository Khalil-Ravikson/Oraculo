"""
Plano A / Fase 3 — circuit breaker por provider LLM (§O / §T).

§T: "circuit breaker abre de fato sob falha simulada do provider ativo e
fecha depois do período de resfriamento."
"""
import time

import pytest

from src.infrastructure.adapters import llm_circuit_breaker as cb


class _FakeRedis:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None):
        self.d[k] = str(v)

    def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)

    def incr(self, k):
        self.d[k] = str(int(self.d.get(k, 0)) + 1)
        return int(self.d[k])

    def expire(self, k, s):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def params_rapidos(monkeypatch):
    from src.infrastructure.settings import settings
    monkeypatch.setattr(settings, "LLM_CB_FALHAS_ABRE", 3, raising=False)
    monkeypatch.setattr(settings, "LLM_CB_COOLDOWN_S", 60, raising=False)
    monkeypatch.setattr(settings, "LLM_CB_JANELA_S", 120, raising=False)


def test_circuito_comeca_fechado(fake_redis):
    assert cb.estado("gemini") == cb.FECHADO
    assert cb.permitir("gemini") is True


def test_abre_apos_n_falhas(fake_redis):
    for _ in range(3):
        cb.registrar_falha("gemini")
    assert cb.estado("gemini") == cb.ABERTO
    assert cb.permitir("gemini") is False


def test_falhas_abaixo_do_limite_nao_abrem(fake_redis):
    cb.registrar_falha("groq")
    cb.registrar_falha("groq")
    assert cb.estado("groq") == cb.FECHADO


def test_meio_aberto_apos_cooldown(fake_redis):
    for _ in range(3):
        cb.registrar_falha("deepseek")
    assert cb.estado("deepseek") == cb.ABERTO
    # "passa" o cooldown reescrevendo o timestamp de abertura pra 61s atrás
    fake_redis.d["cb:llm:deepseek:aberto_em"] = str(time.time() - 61)
    assert cb.estado("deepseek") == cb.MEIO_ABERTO
    assert cb.permitir("deepseek") is True   # deixa 1 tentativa passar


def test_sucesso_fecha_o_circuito(fake_redis):
    for _ in range(3):
        cb.registrar_falha("gemini")
    assert cb.estado("gemini") == cb.ABERTO
    cb.registrar_sucesso("gemini")
    assert cb.estado("gemini") == cb.FECHADO
    assert cb.permitir("gemini") is True


def test_nao_levanta_com_redis_fora(monkeypatch):
    def _boom():
        raise ConnectionError("redis fora")
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", _boom)
    # fail-open: tudo degrada pra FECHADO / permitir, sem exceção
    assert cb.estado("gemini") == cb.FECHADO
    assert cb.permitir("gemini") is True
    cb.registrar_falha("gemini")   # não levanta
    cb.registrar_sucesso("gemini")


def test_monitored_provider_registra_falha_e_sucesso(fake_redis):
    from src.infrastructure.adapters.llm_factory import MonitoredLLMProvider
    from src.domain.ports.llm_Provider import LLMResponse

    class _FakeProvider:
        provider_name = "groq"
        model = "x"
        def __init__(self): self.vai_falhar = False
        def gerar_resposta_sincrono(self, *a, **k):
            if self.vai_falhar:
                raise RuntimeError("provider caiu")
            return LLMResponse(conteudo="ok", model="x", input_tokens=1, output_tokens=1)

    fp = _FakeProvider()
    m = MonitoredLLMProvider(fp)

    fp.vai_falhar = True
    for _ in range(3):
        with pytest.raises(RuntimeError):
            m.gerar_resposta_sincrono("oi")
    assert cb.estado("groq") == cb.ABERTO

    fp.vai_falhar = False
    m.gerar_resposta_sincrono("oi")
    assert cb.estado("groq") == cb.FECHADO
