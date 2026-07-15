# tests/unit/agents/test_conversation_registration.py
"""
Testes do RegistrationFunnel (Fase 6 do PLANO_REFATORACAO_SUPERVISOR.md,
movido de application/routing/registration_funnel.py para
agents/conversation/registration.py). Sem cobertura anterior — primeira vez
que este fluxo é testado isoladamente.
"""
import pytest
from unittest.mock import AsyncMock, patch

from src.agents.conversation.registration import RegistrationFunnel


class FakeRedis:
    def __init__(self):
        self.db = {}

    def get(self, key):
        return self.db.get(key)

    def setex(self, key, ttl, value):
        self.db[key] = value

    def delete(self, key):
        self.db.pop(key, None)


@pytest.mark.asyncio
async def test_start_pede_nome():
    funnel = RegistrationFunnel()
    redis = FakeRedis()

    resposta = await funnel.process("5599999999", "", "Fulano", redis)

    assert "nome completo" in resposta
    assert redis.get("register:step:5599999999") == "awaiting_name"


@pytest.mark.asyncio
async def test_nome_curto_pede_novamente():
    funnel = RegistrationFunnel()
    redis = FakeRedis()
    redis.setex("register:step:5599999999", 600, "awaiting_name")

    resposta = await funnel.process("5599999999", "Jo", "Fulano", redis)

    assert "nome completo" in resposta
    assert redis.get("register:step:5599999999") == "awaiting_name"


@pytest.mark.asyncio
async def test_nome_valido_avanca_para_curso():
    funnel = RegistrationFunnel()
    redis = FakeRedis()
    redis.setex("register:step:5599999999", 600, "awaiting_name")

    resposta = await funnel.process("5599999999", "joao da silva", "Fulano", redis)

    assert "curso" in resposta.lower()
    assert redis.get("register:step:5599999999") == "awaiting_course"
    assert redis.get("register:name:5599999999") == "Joao Da Silva"


@pytest.mark.asyncio
async def test_curso_valido_salva_e_envia_botoes():
    funnel = RegistrationFunnel()
    redis = FakeRedis()
    redis.setex("register:step:5599999999", 600, "awaiting_course")
    redis.setex("register:name:5599999999", 600, "Joao Da Silva")

    with patch("src.capabilities.persistence.registration_repository.salvar_pessoa", new=AsyncMock()) as mock_salvar, \
         patch("src.capabilities.messaging.evolution_tool.enviar_botoes_confirmacao", new=AsyncMock()) as mock_botoes:

        resposta = await funnel.process("5599999999", "engenharia de computacao", "Fulano", redis)

    mock_salvar.assert_awaited_once_with("5599999999", "Joao Da Silva", "Engenharia De Computacao")
    mock_botoes.assert_awaited_once()
    assert resposta == ""  # resposta já foi enviada via botões
    assert redis.get("register:step:5599999999") is None  # estado limpo


@pytest.mark.asyncio
async def test_curso_valido_com_falha_no_salvar_nao_confirma_e_preserva_estado():
    """Sprint 3 (Fase 0) — se salvar_pessoa falhar (ex: IntegrityError), o
    funil não pode confirmar sucesso nem limpar o estado do Redis."""
    funnel = RegistrationFunnel()
    redis = FakeRedis()
    redis.setex("register:step:5599999999", 600, "awaiting_course")
    redis.setex("register:name:5599999999", 600, "Joao Da Silva")

    with patch(
        "src.capabilities.persistence.registration_repository.salvar_pessoa",
        new=AsyncMock(side_effect=RuntimeError("IntegrityError simulado")),
    ):
        resposta = await funnel.process("5599999999", "engenharia de computacao", "Fulano", redis)

    assert "problema técnico" in resposta.lower()
    assert "Cadastro realizado" not in resposta
    assert redis.get("register:step:5599999999") == "awaiting_course"  # estado preservado


@pytest.mark.asyncio
async def test_curso_valido_fallback_texto_quando_botoes_falham():
    funnel = RegistrationFunnel()
    redis = FakeRedis()
    redis.setex("register:step:5599999999", 600, "awaiting_course")
    redis.setex("register:name:5599999999", 600, "Joao Da Silva")

    with patch("src.capabilities.persistence.registration_repository.salvar_pessoa", new=AsyncMock()), \
         patch("src.capabilities.messaging.evolution_tool.enviar_botoes_confirmacao", new=AsyncMock(side_effect=RuntimeError("gateway offline"))):

        resposta = await funnel.process("5599999999", "engenharia de computacao", "Fulano", redis)

    assert "Cadastro realizado" in resposta
    assert "Joao" in resposta
