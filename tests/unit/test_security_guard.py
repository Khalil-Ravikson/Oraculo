"""
tests/unit/test_security_guard.py
-----------------------------------
Testes do SecurityGuard: RBAC, Rate Limit, Comandos Admin.
Sem Redis real (usa FakeRedis do conftest).

Execute: pytest tests/unit/test_security_guard.py -v
"""
import pytest
from src.api.middleware.security_guard import NivelAcesso, SecurityGuard


@pytest.fixture
def settings_mock():
    from unittest.mock import MagicMock
    s = MagicMock()
    s.ADMIN_NUMBERS = "5598000000001"
    s.STUDENT_NUMBERS = "5598000000002"
    return s


@pytest.fixture
def guard(fake_redis, settings_mock) -> SecurityGuard:
    return SecurityGuard(fake_redis, settings_mock)


@pytest.mark.unit
class TestRBAC:
    def test_admin_reconhecido(self, guard):
        r = guard.verificar(user_id="5598000000001", body="oi")
        assert r.nivel == NivelAcesso.ADMIN

    def test_student_reconhecido(self, guard):
        r = guard.verificar(user_id="5598000000002", body="oi")
        assert r.nivel == NivelAcesso.STUDENT

    def test_desconhecido_e_guest(self, guard):
        r = guard.verificar(user_id="5598999999999", body="oi")
        assert r.nivel == NivelAcesso.GUEST

    def test_admin_tem_todas_as_tools(self, guard):
        r = guard.verificar(user_id="5598000000001", body="oi")
        assert "consultar_calendario_academico" in r.tools_disponiveis
        assert "abrir_chamado_glpi" in r.tools_disponiveis
        assert "admin_limpar_cache" in r.tools_disponiveis

    def test_student_tem_glpi(self, guard):
        r = guard.verificar(user_id="5598000000002", body="oi")
        assert "abrir_chamado_glpi" in r.tools_disponiveis
        assert "admin_limpar_cache" not in r.tools_disponiveis

    def test_guest_nao_tem_glpi(self, guard):
        r = guard.verificar(user_id="5598999999999", body="oi")
        assert "abrir_chamado_glpi" not in r.tools_disponiveis


@pytest.mark.unit
class TestRateLimit:
    def test_guest_bloqueado_apos_limite(self, fake_redis, settings_mock):
        guard = SecurityGuard(fake_redis, settings_mock)
        phone = "5598111111111"  # GUEST desconhecido
        # Simula 11 mensagens (limite GUEST = 10/min)
        for _ in range(10):
            r = guard.verificar(user_id=phone, body="teste")
            assert not r.bloqueado
        # 11ª mensagem deve ser bloqueada
        r = guard.verificar(user_id=phone, body="teste")
        assert r.bloqueado
        assert r.acao == "BLOQUEADO"

    def test_admin_nao_tem_rate_limit(self, fake_redis, settings_mock):
        guard = SecurityGuard(fake_redis, settings_mock)
        phone = "5598000000001"  # ADMIN
        for _ in range(20):  # muito além do limite normal
            r = guard.verificar(user_id=phone, body="teste")
            assert not r.bloqueado

    def test_mensagem_bloqueio_presente(self, fake_redis, settings_mock):
        guard = SecurityGuard(fake_redis, settings_mock)
        phone = "5598222222222"  # GUEST
        for _ in range(11):
            r = guard.verificar(user_id=phone, body="teste")
        assert r.bloqueado
        assert r.resposta  # mensagem de erro


@pytest.mark.unit
class TestComandosAdmin:
    def test_status_reconhecido(self, guard):
        r = guard.verificar(user_id="5598000000001", body="!status")
        assert r.acao == "CMD_ADMIN"
        assert r.parametro == "STATUS"

    def test_tools_reconhecido(self, guard):
        r = guard.verificar(user_id="5598000000001", body="!tools")
        assert r.acao == "CMD_ADMIN"
        assert r.parametro == "TOOLS"

    def test_limpar_cache_reconhecido(self, guard):
        r = guard.verificar(user_id="5598000000001", body="!limpar_cache")
        assert r.acao == "CMD_ADMIN"
        assert r.precisa_celery is True

    def test_ingerir_sem_arquivo_retorna_erro(self, guard):
        r = guard.verificar(user_id="5598000000001", body="!ingerir")
        assert r.acao == "ERRO"

    def test_ingerir_com_nome_arquivo(self, guard):
        r = guard.verificar(user_id="5598000000001", body="!ingerir edital.pdf")
        assert r.acao in ("INGERIR_FICHEIRO", "CMD_ADMIN")

    def test_guest_nao_processa_comandos_admin(self, guard):
        r = guard.verificar(user_id="5598999999999", body="!status")
        # GUEST não deve ter comandos admin processados — vai para LLM normal
        assert r.acao == "LLM"

    def test_student_nao_processa_comandos_admin(self, guard):
        r = guard.verificar(user_id="5598000000002", body="!limpar_cache")
        assert r.acao == "LLM"

    def test_llm_normal_retorna_acao_llm(self, guard):
        r = guard.verificar(user_id="5598000000001", body="quando é a matrícula?")
        assert r.acao == "LLM"
        assert not r.bloqueado