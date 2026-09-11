"""O passo de menu dentro do `entrypoint.py` (item B2).

Enquanto `test_menu_resolver.py` prova que a DECISÃO está certa, este arquivo
prova o que essa decisão faz com o pipeline — em particular o invariante que
justifica o v1 inteiro:

    **navegar o menu não invoca o grafo, logo não gasta token.**

O grafo aqui é um dublê que EXPLODE se for chamado. É essa explosão, e não
uma asserção sobre contagem de tokens, que prova o invariante: token só é
gasto dentro do grafo.
"""
from __future__ import annotations

import pytest

from src.application.orchestration import entrypoint as ep


class _RedisFake:
    """Redis de mentira, em memória. Só o que `menu/state.py` e o check de
    handoff usam."""

    def __init__(self) -> None:
        self.dados: dict[str, str] = {}

    def exists(self, chave: str) -> int:
        return 1 if chave in self.dados else 0

    def get(self, chave: str) -> str | None:
        return self.dados.get(chave)

    def setex(self, chave: str, ttl: int, valor: str) -> None:
        self.dados[chave] = valor

    def delete(self, chave: str) -> None:
        self.dados.pop(chave, None)


class _GrafoProibido:
    """Invocar isto é o próprio bug que estes testes procuram."""

    async def aget_state(self, config):  # noqa: ANN001
        class _Estado:
            next = ()
            values: dict = {}
            tasks = ()

        return _Estado()

    async def ainvoke(self, payload, config):  # noqa: ANN001
        raise AssertionError(
            f"o grafo foi invocado numa navegação de menu (payload={payload!r}) — "
            "isso é uma chamada de LLM que não deveria existir"
        )


class _GrafoEspiao(_GrafoProibido):
    """Registra o payload em vez de explodir, para os casos em que invocar o
    grafo é o comportamento certo (pergunta e handoff)."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def ainvoke(self, payload, config):  # noqa: ANN001
        self.payloads.append(payload)
        return {"answer": "resposta do grafo", "rota": payload.get("rota", "")}


@pytest.fixture
def redis_fake(monkeypatch) -> _RedisFake:
    r = _RedisFake()
    monkeypatch.setattr("src.infrastructure.redis_client.get_redis_text", lambda: r)
    return r


def _instalar_grafo(monkeypatch, grafo) -> None:
    async def _get_graph():
        return grafo

    monkeypatch.setattr(ep, "_get_graph", _get_graph)


@pytest.mark.asyncio
async def test_primeira_mensagem_responde_o_menu_sem_tocar_no_grafo(
    monkeypatch, redis_fake
) -> None:
    _instalar_grafo(monkeypatch, _GrafoProibido())

    r = await ep.processar("oi", "sess-menu-1", {})

    assert r.plan_id == "menu"
    assert r.rota == "MENU"
    assert "Sou o Oráculo" in r.answer


@pytest.mark.asyncio
async def test_navegar_submenus_nao_toca_no_grafo(monkeypatch, redis_fake) -> None:
    _instalar_grafo(monkeypatch, _GrafoProibido())
    sessao = "sess-menu-2"

    await ep.processar("oi", sessao, {})
    dentro = await ep.processar("2", sessao, {})
    assert "SIGAA" in dentro.answer

    voltou = await ep.processar("9", sessao, {})
    assert "Sou o Oráculo" in voltou.answer


@pytest.mark.asyncio
async def test_texto_fixo_nao_toca_no_grafo(monkeypatch, redis_fake) -> None:
    _instalar_grafo(monkeypatch, _GrafoProibido())
    sessao = "sess-menu-3"

    await ep.processar("oi", sessao, {})
    await ep.processar("1", sessao, {})
    r = await ep.processar("4", sessao, {})  # Wi-Fi

    assert r.plan_id == "menu"
    assert r.answer


@pytest.mark.asyncio
async def test_convite_de_pergunta_ainda_nao_toca_no_grafo(monkeypatch, redis_fake) -> None:
    """Apertar "SIGAA" só pede a pergunta. O LLM ainda não entra."""
    _instalar_grafo(monkeypatch, _GrafoProibido())
    sessao = "sess-menu-4"

    await ep.processar("oi", sessao, {})
    await ep.processar("2", sessao, {})
    r = await ep.processar("1", sessao, {})

    assert "Escreva sua pergunta sobre o SIGAA" in r.answer


@pytest.mark.asyncio
async def test_pergunta_invoca_o_grafo_ja_classificado(monkeypatch, redis_fake) -> None:
    """O único caminho que chega ao grafo, e ele chega com a rota decidida —
    `classify_node` não classifica nada, então o Supervisor não roda."""
    espiao = _GrafoEspiao()
    _instalar_grafo(monkeypatch, espiao)
    sessao = "sess-menu-5"

    await ep.processar("oi", sessao, {})
    await ep.processar("2", sessao, {})
    await ep.processar("1", sessao, {})
    r = await ep.processar("como emito declaração?", sessao, {})

    assert len(espiao.payloads) == 1
    payload = espiao.payloads[0]
    assert payload["route"] == "rag"
    assert payload["rota"] == "WIKI"
    assert payload["message"] == "como emito declaração?"
    assert payload["user_context"]["menu_filtros"] == {"sistema": "sigaa"}
    # A resposta do grafo sai inteira; o que vem depois dela é a tela de
    # feedback do item B6, coberta mais abaixo.
    assert r.answer.startswith("resposta do grafo")


@pytest.mark.asyncio
async def test_handoff_invoca_o_no_de_atendente_direto(monkeypatch, redis_fake) -> None:
    espiao = _GrafoEspiao()
    _instalar_grafo(monkeypatch, espiao)
    sessao = "sess-menu-6"

    await ep.processar("oi", sessao, {})
    await ep.processar("0", sessao, {})

    assert espiao.payloads[0]["route"] == "human_handoff"
    assert espiao.payloads[0]["rota"] == "ESCALAR_HUMANO"


@pytest.mark.asyncio
async def test_posicao_do_menu_sobrevive_entre_mensagens(monkeypatch, redis_fake) -> None:
    _instalar_grafo(monkeypatch, _GrafoProibido())
    sessao = "sess-menu-7"

    await ep.processar("oi", sessao, {})
    await ep.processar("3", sessao, {})

    assert f"menu:{sessao}" in redis_fake.dados


@pytest.mark.asyncio
async def test_sessoes_diferentes_nao_compartilham_posicao(monkeypatch, redis_fake) -> None:
    _instalar_grafo(monkeypatch, _GrafoProibido())

    await ep.processar("oi", "sess-a", {})
    await ep.processar("2", "sess-a", {})
    # "sess-b" nunca navegou: tem que ver o menu principal, não o submenu.
    r = await ep.processar("oi", "sess-b", {})

    assert "Sou o Oráculo" in r.answer


@pytest.mark.asyncio
async def test_flag_desligada_volta_ao_supervisor(monkeypatch, redis_fake) -> None:
    """O interruptor de rollback: com `FEATURE_MENU_BOT=False` o entrypoint
    volta a invocar o grafo sem rota, e quem classifica é o `classify_node`."""
    from src.infrastructure.settings import settings

    monkeypatch.setattr(settings, "FEATURE_MENU_BOT", False)
    espiao = _GrafoEspiao()
    _instalar_grafo(monkeypatch, espiao)

    await ep.processar("oi", "sess-menu-8", {})

    assert len(espiao.payloads) == 1
    assert espiao.payloads[0]["route"] == ""
    assert "rota" not in espiao.payloads[0]


# ── Tela "Isso ajudou?" (B6) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resposta_de_rag_termina_com_a_tela_de_feedback(
    monkeypatch, redis_fake
) -> None:
    _instalar_grafo(monkeypatch, _GrafoEspiao())
    sessao = "sess-fb-1"

    await ep.processar("oi", sessao, {})
    await ep.processar("2", sessao, {})
    await ep.processar("1", sessao, {})
    r = await ep.processar("como emito declaração?", sessao, {})

    assert r.answer.startswith("resposta do grafo")
    assert "Isso ajudou?" in r.answer


@pytest.mark.asyncio
async def test_apos_o_feedback_o_menu_fica_na_tela_certa(monkeypatch, redis_fake) -> None:
    """E responder "9" ali volta ao menu principal, sem tocar no grafo."""
    _instalar_grafo(monkeypatch, _GrafoEspiao())
    sessao = "sess-fb-2"

    await ep.processar("oi", sessao, {})
    await ep.processar("2", sessao, {})
    await ep.processar("1", sessao, {})
    await ep.processar("como emito declaração?", sessao, {})

    voltou = await ep.processar("9", sessao, {})
    assert "Sou o Oráculo" in voltou.answer


@pytest.mark.asyncio
async def test_menu_e_texto_fixo_nao_ganham_tela_de_feedback(
    monkeypatch, redis_fake
) -> None:
    """O rodapé é só de resposta de RAG — perguntar "isso ajudou?" depois de
    imprimir um menu não faz sentido nenhum."""
    _instalar_grafo(monkeypatch, _GrafoProibido())
    sessao = "sess-fb-3"

    r = await ep.processar("oi", sessao, {})
    assert "Isso ajudou?" not in r.answer
