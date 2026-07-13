# tests/unit/agents/academic_knowledge/test_synthesis.py
"""
Testes da SynthesisService consolidada na Fase 4 do
PLANO_REFATORACAO_SUPERVISOR.md — cobre especificamente os overrides
administrativos (admin:gemini_blocked, admin:system_prompt) que antes só
existiam na implementacao dormente e agora valem para o worker_synthesis
real.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.agents.academic_knowledge.synthesis import SynthesisService


@pytest.mark.asyncio
async def test_sintetizar_respeita_kill_switch_admin():
    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda key: "1" if key == "admin:gemini_blocked" else None

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis):
        result = await SynthesisService().sintetizar(chunks=[], plan_ctx={"query": "oi"})

    assert result.ok is True
    assert "manutenção" in result.answer.lower()


@pytest.mark.asyncio
async def test_sintetizar_usa_system_prompt_override():
    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda key: (
        "Prompt customizado" if key == "admin:system_prompt" else None
    )

    mock_response = MagicMock()
    mock_response.text = "resposta gerada"
    mock_response.usage_metadata = MagicMock(prompt_token_count=5, candidates_token_count=3)
    mock_generate = AsyncMock(return_value=mock_response)
    mock_aio = MagicMock(models=MagicMock(generate_content=mock_generate))

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("google.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        mock_client_class.return_value = mock_client

        with patch("src.infrastructure.settings.settings.GEMINI_API_KEY", "fake_key"):
            result = await SynthesisService().sintetizar(
                chunks=[{"content": "texto", "source": "doc.pdf"}],
                plan_ctx={"query": "oi"},
            )

    assert result.ok is True
    assert result.answer == "resposta gerada"
    called_kwargs = mock_generate.call_args.kwargs
    assert called_kwargs["config"].system_instruction == "Prompt customizado"


@pytest.mark.asyncio
async def test_sintetizar_erro_llm_retorna_result_com_error():
    import src.agents.academic_knowledge.synthesis as synthesis_module
    synthesis_module._client = None  # reseta o singleton do client (isolamento entre testes)

    mock_redis = MagicMock()
    mock_redis.get.return_value = None

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("google.genai.Client", side_effect=RuntimeError("boom")):
        result = await SynthesisService().sintetizar(chunks=[], plan_ctx={"query": "oi"})

    assert result.ok is False
    assert "boom" in result.error
