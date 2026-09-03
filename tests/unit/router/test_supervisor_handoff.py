"""
tests/unit/router/test_supervisor_handoff.py
============================================
Rota ESCALAR_HUMANO na camada 1 (regex) do Supervisor (ADR 0008 Fase 2).
"""
from __future__ import annotations

import pytest

from src.router.supervisor import _regex_rapido, _RE_HUMANO


@pytest.mark.parametrize("frase", [
    "quero falar com um atendente",
    "me passa pra uma pessoa de verdade",
    "preciso falar com alguém",
    "quero um humano",
    "não quero falar com robô",
    "pode me transferir pra um atendente humano?",
])
def test_pedido_de_humano_vira_escalar_humano(frase):
    assert _regex_rapido(frase) == "ESCALAR_HUMANO"


@pytest.mark.parametrize("frase", [
    "qual o telefone do atendente da biblioteca?",
    "quem é o responsável pela PROG?",
    "quando começa a matrícula?",
    "quero abrir um chamado",
])
def test_nao_confunde_com_pergunta_normal(frase):
    assert _regex_rapido(frase) != "ESCALAR_HUMANO"


def test_regex_isolado():
    assert _RE_HUMANO.search("falar com atendente")
    assert not _RE_HUMANO.search("atendente do setor de contatos")
