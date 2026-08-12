import json
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.application.workers.worker_text_to_audio import _run
from src.infrastructure.services.audio_service import AudioResult


@pytest.mark.asyncio
async def test_run_apaga_arquivo_temp_e_persiste_audio_b64_no_redis():
    """
    Bug real corrigido nesta sessão: o worker deixava o arquivo temp em /tmp
    pra "quem consumir apagar" — mas quem despacha esse worker via Celery
    (ex.: process_message_task.py) roda num container DIFERENTE (worker
    `default` vs worker `media`), sem acesso a esse caminho local. E o
    audio_b64 era explicitamente filtrado antes de salvar no Redis, então
    não tinha como o consumidor recuperar o áudio de jeito nenhum.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(b"fake-wav-bytes")
    tmp.close()

    fake_svc = MagicMock()
    fake_svc.synthesize = AsyncMock(return_value=AudioResult(ok=True, audio_path=tmp.name))

    fake_redis = MagicMock()

    with patch(
        "src.infrastructure.services.audio_service.get_audio_service",
        return_value=fake_svc,
    ), patch(
        "src.infrastructure.redis_client.get_redis_text",
        return_value=fake_redis,
    ):
        payload = await _run({
            "plan_id": "p1", "session_id": "s1", "step_id": "s_tts",
            "text": "ola", "lang": "pt",
        })

    assert payload["status"] == "ok"
    assert payload["audio_b64"]
    assert not os.path.exists(tmp.name)

    fake_redis.setex.assert_called_once()
    key, _ttl, raw_json = fake_redis.setex.call_args.args
    assert key == "plan:results:p1:s_tts"
    saved = json.loads(raw_json)
    assert "audio_b64" in saved


@pytest.mark.asyncio
async def test_run_falha_sintese_nao_gera_audio_b64():
    fake_svc = MagicMock()
    fake_svc.synthesize = AsyncMock(return_value=AudioResult(ok=False, error="kokoro indisponível"))

    with patch(
        "src.infrastructure.services.audio_service.get_audio_service",
        return_value=fake_svc,
    ), patch(
        "src.infrastructure.redis_client.get_redis_text",
        return_value=MagicMock(),
    ):
        payload = await _run({
            "plan_id": "p1", "session_id": "s1", "step_id": "s_tts",
            "text": "ola", "lang": "pt",
        })

    assert payload["status"] == "error"
    assert "audio_b64" not in payload
