# tests/unit/application/test_dispatcher.py
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.runtime.dispatcher import processar, OSResult

class MockRedis:
    def __init__(self):
        self.db = {}
    def get(self, key):
        val = self.db.get(key)
        if val is None:
            return None
        return val.encode('utf-8') if isinstance(val, str) else val
    def setex(self, key, time, value):
        self.db[key] = value
    def exists(self, key):
        return key in self.db
    def delete(self, key):
        self.db.pop(key, None)
    def xadd(self, name, fields, **kwargs):
        pass

@pytest.mark.asyncio
async def test_cognitive_os_sigaa_route_requires_auth_flow():
    # Mocking redis
    mock_redis = MockRedis()
    
    # Mocking Router decision to return SIGAA route
    mock_decision = MagicMock()
    mock_decision.rota = "SIGAA"
    mock_decision.cache_hit = False
    mock_decision.dag_hint = {}
    
    user_context = {"role": "student"}
    session_id = "test_whatsapp_session"
    
    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("src.router.supervisor.rotear", return_value=mock_decision):
        
        # 1. First prompt: "qual meu CR?" -> Should prompt for CPF
        res = await processar("qual meu CR?", session_id, user_context)
        assert res.status == "hitl_pending"
        assert "Autenticação Requerida" in res.answer
        assert "CPF" in res.answer
        assert mock_redis.exists(f"hitl:session:{session_id}")
        
        # Check that stored state is sigaa_collect_cpf
        state = json.loads(mock_redis.get(f"hitl:session:{session_id}").decode())
        assert state["action"] == "sigaa_collect_cpf"
        assert state["target_action"] == "sigaa_indice"
        
        # 2. Invalid CPF entry: "123" -> Should complain and ask again
        res2 = await processar("123", session_id, user_context)
        assert res2.status == "hitl_pending"
        assert "CPF Inválido" in res2.answer
        
        # 3. Valid CPF entry: "12345678901" -> Should transition to AWAITING_PASSWORD
        res3 = await processar("12345678901", session_id, user_context)
        assert res3.status == "hitl_pending"
        assert "senha" in res3.answer
        
        state2 = json.loads(mock_redis.get(f"hitl:session:{session_id}").decode())
        assert state2["action"] == "sigaa_collect_password"
        assert state2["cpf"] == "12345678901"
        
        # 4. Password entry: "secret123" -> Should dispatch task and clear session
        with patch("celery.chain") as mock_chain, \
             patch("src.application.workers.registry._REGISTRY") as mock_registry:
            
            mock_task = MagicMock()
            mock_registry.get.return_value = mock_task
            
            res4 = await processar("secret123", session_id, user_context)
            assert res4.status == "ok"
            assert "Autenticação em andamento" in res4.answer
            assert not mock_redis.exists(f"hitl:session:{session_id}")

            # TD-017: a senha NÃO vai mais em texto plano no payload da task
            # Celery — fica no Redis sob um `auth_token` de uso único
            # (auth_flow.py, melhoria de segurança). O evento só carrega o token.
            mock_task.s.assert_called_once()
            args, kwargs = mock_task.s.call_args
            event_sent = args[0]
            assert event_sent["login"] == "12345678901"
            assert "senha" not in event_sent
            assert event_sent["hitl_confirmed"] is True

            auth_token = event_sent["auth_token"]
            guardado = json.loads(mock_redis.get(f"hitl:auth_token:{auth_token}").decode())
            assert guardado["senha"] == "secret123"

@pytest.mark.asyncio
async def test_cognitive_os_sigaa_route_with_active_session():
    # Mocking redis with active session cookies
    mock_redis = MockRedis()
    session_key = f"sigaa:session:test_whatsapp_session"
    mock_redis.setex(session_key, 1200, "some_cookies")
    
    # Mocking Router decision to return SIGAA route
    mock_decision = MagicMock()
    mock_decision.rota = "SIGAA"
    mock_decision.cache_hit = False
    mock_decision.dag_hint = {}
    
    user_context = {"role": "student"}
    session_id = "test_whatsapp_session"
    
    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("src.router.supervisor.rotear", return_value=mock_decision), \
         patch("celery.chain") as mock_chain, \
         patch("src.application.workers.registry._REGISTRY") as mock_registry:
             
        mock_task = MagicMock()
        mock_registry.get.return_value = mock_task
        
        # Should bypass CPF prompt completely and dispatch task
        res = await processar("qual meu CR?", session_id, user_context)
        assert res.status == "ok"
        assert "Utilizando sua sessão ativa" in res.answer
        
        mock_task.s.assert_called_once()
        args, kwargs = mock_task.s.call_args
        event_sent = args[0]
        assert event_sent["session_id"] == session_id
        assert not mock_redis.exists(f"hitl:session:{session_id}")


@pytest.mark.asyncio
async def test_processar_check_status_responde_sem_planner():
    """Fusão Router+Orquestrador (notas.md §5.1): quando `rotear()` decide
    CHECK_STATUS, `processar()` deve responder direto a partir de
    task_history, sem chamar Planner/Celery — mesmo comportamento que o
    antigo `orchestrate(action="check_status")` tinha, agora vindo de uma
    única chamada de roteamento."""
    mock_redis = MockRedis()

    mock_decision = MagicMock()
    mock_decision.rota = "CHECK_STATUS"
    mock_decision.cache_hit = True
    mock_decision.dag_hint = {}

    fake_mem = MagicMock()
    fake_mem.set_operational  = AsyncMock(return_value=None)
    fake_mem.get_task_history = AsyncMock(
        return_value={"last_worker": "rag_search", "last_result": "encontrei o edital do PAES"}
    )

    with patch("src.infrastructure.redis_client.get_redis_text", return_value=mock_redis), \
         patch("src.memory.services.redis_memory_service.get_cognitive_memory", return_value=fake_mem), \
         patch("src.router.supervisor.rotear", return_value=mock_decision), \
         patch("src.application.chain.planner.criar_plano") as mock_planner:

        res = await processar("e aí, já saiu?", "session-1", {"role": "student"})

        assert res.status == "ok"
        assert res.plan_id == "check_status"
        assert res.rota == "CHECK_STATUS"
        assert "rag_search" in res.answer
        assert "encontrei o edital do PAES" in res.answer
        mock_planner.assert_not_called()
