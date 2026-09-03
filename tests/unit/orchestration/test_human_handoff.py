"""
tests/unit/orchestration/test_human_handoff.py
==============================================
Nó `human_handoff` (ADR 0008 Fase 2): silencia o bot pra a sessão, registra
na fila, avisa o suporte. + mute no entrypoint + comando `$voltar`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.orchestration.nodes import human_handoff_node
from src.application.orchestration.state import OraculoState


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.streams = {}

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def exists(self, k):
        return 1 if k in self.kv else 0

    def delete(self, k):
        return 1 if self.kv.pop(k, None) is not None else 0

    def xadd(self, stream, fields, **kw):
        self.streams.setdefault(stream, []).append(fields)

    def scan(self, cursor, match=None, count=None):
        import fnmatch
        keys = [k for k in self.kv if match is None or fnmatch.fnmatch(k, match)]
        return 0, keys


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: r)
    return r


@pytest.mark.asyncio
async def test_handoff_node_silencia_registra_e_avisa(fake_redis, monkeypatch):
    from src.infrastructure import settings as sm
    monkeypatch.setattr(sm.settings, "SUPPORT_GROUP_JID", "sup@g.us")

    enviar = AsyncMock()
    with patch("src.infrastructure.adapters.evolution_adapter.EvolutionAdapter") as Adapter, \
         patch("src.memory.services.redis_memory_service.get_cognitive_memory") as mem:
        Adapter.return_value.enviar_mensagem = enviar
        mem.return_value.format_history = AsyncMock(return_value="")
        state = OraculoState(
            session_id="5598999@s.whatsapp.net", message="quero falar com um atendente",
            route="human_handoff", user_context={"nome": "Ana", "chat_id": "grp@g.us"},
        )
        out = await human_handoff_node(state)

    assert out["status"] == "handoff"
    assert out["handoff"] is True
    assert "atendente humano" in out["answer"].lower()
    assert fake_redis.exists("handoff:session:5598999@s.whatsapp.net") == 1
    assert len(fake_redis.streams["handoff:queue"]) == 1
    enviar.assert_awaited_once()
    assert enviar.call_args[0][0] == "sup@g.us"


@pytest.mark.asyncio
async def test_handoff_sem_destino_nao_quebra(fake_redis, monkeypatch):
    from src.infrastructure import settings as sm
    monkeypatch.setattr(sm.settings, "SUPPORT_GROUP_JID", "")
    monkeypatch.setattr(sm.settings, "ADMIN_NUMBERS", "")

    with patch("src.memory.services.redis_memory_service.get_cognitive_memory") as mem:
        mem.return_value.format_history = AsyncMock(return_value="")
        state = OraculoState(session_id="s1", message="atendimento humano", route="human_handoff")
        out = await human_handoff_node(state)

    assert out["status"] == "handoff"
    assert fake_redis.exists("handoff:session:s1") == 1


@pytest.mark.asyncio
async def test_entrypoint_muta_sessao_em_handoff(fake_redis):
    import src.application.orchestration.entrypoint as ep
    fake_redis.set("handoff:session:s-mudo", "1")

    res = await ep.processar("qualquer coisa", "s-mudo", {})

    assert res.status == "handoff"
    assert res.answer == ""
    assert res.plan_id == "handoff_muted"


@pytest.mark.asyncio
async def test_comando_voltar_remove_o_mute(fake_redis):
    from src.application.commands.cmd_handoff import CmdVoltar
    from src.application.routing.command_builder import CommandContext

    fake_redis.set("handoff:session:jid-1", "1")
    ctx = CommandContext(sender_jid="admin", chat_id="admin", text="jid-1", redis_text=fake_redis)
    msg = await CmdVoltar().execute(ctx)

    assert "devolvida" in msg.lower()
    assert fake_redis.exists("handoff:session:jid-1") == 0


@pytest.mark.asyncio
async def test_comando_voltar_sem_arg_lista(fake_redis):
    from src.application.commands.cmd_handoff import CmdVoltar
    from src.application.routing.command_builder import CommandContext

    fake_redis.set("handoff:session:a@x", "1")
    fake_redis.set("handoff:session:b@x", "1")
    ctx = CommandContext(sender_jid="admin", chat_id="admin", text="", redis_text=fake_redis)
    msg = await CmdVoltar().execute(ctx)

    assert "a@x" in msg and "b@x" in msg
