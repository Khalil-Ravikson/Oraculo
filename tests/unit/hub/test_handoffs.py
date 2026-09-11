"""A saída do modo "atendimento humano" pelo painel (2026-09-10).

Contexto do achado: quando alguém pede um atendente, o bot silencia a conversa
por 24h. Até esta data havia UMA saída antes do prazo — `$voltar <jid>`, um
comando de admin do WhatsApp. Quem testava pelo simulador de chat do painel
ficava presa, porque o simulador não passa pelo gatekeeper de comandos.
Aconteceu de verdade: `web_session_admin_uema_khalil` ficou muda por 22 horas.

Estes testes protegem a terceira saída — a página `/hub/handoffs` — e a
propriedade que importa: **devolver ao bot é apagar a chave de pausa**, o
mesmo efeito do comando, sem depender de decorar um identificador.
"""
from __future__ import annotations

import pytest


class _RedisFake:
    """Redis mínimo com o que os endpoints usam: scan, ttl e delete."""

    def __init__(self, chaves: dict[str, int] | None = None):
        self.chaves = dict(chaves or {})

    def scan(self, cursor, match=None, count=None):
        # Um cursor só: devolve tudo e sinaliza fim. Suficiente — o endpoint
        # itera até cursor 0, e o teste não é sobre paginação do Redis.
        prefixo = (match or "").rstrip("*")
        return 0, [k for k in self.chaves if k.startswith(prefixo)]

    def ttl(self, chave):
        return self.chaves.get(chave, -2)

    def delete(self, chave):
        return 1 if self.chaves.pop(chave, None) is not None else 0


@pytest.fixture
def redis_fake(monkeypatch):
    fake = _RedisFake({
        "handoff:session:5598999@s.whatsapp.net": 80000,
        "handoff:session:web_session_admin": 79000,
    })
    import src.infrastructure.redis_client as rc

    monkeypatch.setattr(rc, "get_redis_text", lambda: fake)
    return fake


def _payload_admin(monkeypatch):
    """Passa pela verificação de cookie sem montar um JWT de verdade."""
    import src.api.routers.web.hub as hub

    class _P:
        sub = "admin_teste"

    monkeypatch.setattr(hub, "_verificar_cookie", lambda request: _P())


@pytest.mark.asyncio
async def test_lista_separa_painel_de_whatsapp(redis_fake, monkeypatch):
    """A origem importa: só o WhatsApp tem alguém de verdade esperando do
    outro lado. Uma sessão do simulador quase sempre é resíduo de teste."""
    import src.api.routers.web.hub as hub

    _payload_admin(monkeypatch)
    r = await hub.handoffs_data(request=None)

    por_id = {s["session_id"]: s for s in r["sessoes"]}
    assert por_id["5598999@s.whatsapp.net"]["origem"] == "whatsapp"
    assert por_id["web_session_admin"]["origem"] == "painel"


@pytest.mark.asyncio
async def test_whatsapp_aparece_antes_do_simulador(redis_fake, monkeypatch):
    """Quem tem gente esperando vem primeiro na tela."""
    import src.api.routers.web.hub as hub

    _payload_admin(monkeypatch)
    r = await hub.handoffs_data(request=None)

    assert r["sessoes"][0]["origem"] == "whatsapp"


@pytest.mark.asyncio
async def test_devolver_apaga_a_pausa(redis_fake, monkeypatch):
    """É o mesmo efeito de `$voltar <jid>`: a chave some, e a próxima
    mensagem daquela pessoa volta a ser respondida."""
    import src.api.routers.web.hub as hub

    _payload_admin(monkeypatch)
    r = await hub.handoffs_devolver(
        request=None,
        data=hub.HandoffDevolverRequest(session_id="web_session_admin"),
    )

    assert r["ok"] is True
    assert "handoff:session:web_session_admin" not in redis_fake.chaves


@pytest.mark.asyncio
async def test_devolver_duas_vezes_avisa_em_vez_de_mentir(redis_fake, monkeypatch):
    """Segundo clique não pode responder "ok" — quem opera precisa saber que
    a conversa já tinha voltado."""
    import src.api.routers.web.hub as hub

    _payload_admin(monkeypatch)
    pedido = hub.HandoffDevolverRequest(session_id="web_session_admin")

    await hub.handoffs_devolver(request=None, data=pedido)
    segunda = await hub.handoffs_devolver(request=None, data=pedido)

    assert "error" in segunda
    assert "ok" not in segunda


@pytest.mark.asyncio
async def test_devolver_sessao_vazia_e_recusado(redis_fake, monkeypatch):
    import src.api.routers.web.hub as hub

    _payload_admin(monkeypatch)
    r = await hub.handoffs_devolver(
        request=None, data=hub.HandoffDevolverRequest(session_id="   "),
    )

    assert "error" in r


@pytest.mark.asyncio
async def test_redis_fora_nao_derruba_a_pagina(monkeypatch):
    """A página precisa abrir mesmo com o Redis fora — quem chega aqui já
    está tentando resolver um problema."""
    import src.api.routers.web.hub as hub
    import src.infrastructure.redis_client as rc

    _payload_admin(monkeypatch)

    def explode():
        raise RuntimeError("sem redis")

    monkeypatch.setattr(rc, "get_redis_text", explode)
    r = await hub.handoffs_data(request=None)

    assert r["sessoes"] == []
    assert "error" in r
