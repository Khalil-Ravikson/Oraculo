# tests/unit/application/test_cognitive_os.py
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.chain.cognitive_os import processar, OSResult

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
         patch("src.application.routing.semantic_router.rotear", return_value=mock_decision):
        
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
            
            # Verify dispatch arguments (login/senha in event)
            mock_task.s.assert_called_once()
            args, kwargs = mock_task.s.call_args
            event_sent = args[0]
            assert event_sent["login"] == "12345678901"
            assert event_sent["senha"] == "secret123"
            assert event_sent["hitl_confirmed"] is True

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
         patch("src.application.routing.semantic_router.rotear", return_value=mock_decision), \
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
