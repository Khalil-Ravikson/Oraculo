import pytest

from src.application.runtime.dispatcher import _quer_resposta_em_audio, _remover_pedido_audio


@pytest.mark.parametrize("texto", [
    "responda em áudio",
    "me explica em áudio o processo de matrícula",
    "manda um áudio explicando",
    "mande áudio por favor",
    "pode mandar isso por áudio?",
    "em forma de áudio, por favor",
])
def test_detecta_pedido_de_audio(texto):
    assert _quer_resposta_em_audio(texto) is True


@pytest.mark.parametrize("texto", [
    "qual o horário da biblioteca",
    "o áudio que mandei não carregou",
    "preciso de ajuda com o sigaa",
    "",
])
def test_nao_detecta_falso_positivo(texto):
    assert _quer_resposta_em_audio(texto) is False


def test_remove_pedido_audio_mantem_pergunta_substantiva():
    """
    Bug real de produção: sem essa limpeza, o LLM de síntese via a frase
    completa ("Me explique em áudio sobre o Office 365") e respondia SOBRE
    o pedido de áudio ("não consigo te explicar em áudio, sou um assistente
    de texto") em vez de responder a pergunta de verdade.
    """
    assert _remover_pedido_audio("Me explique em áudio sobre o Office 365") == \
        "Me explique sobre o Office 365"


def test_remove_pedido_audio_manda_um_audio():
    resultado = _remover_pedido_audio("manda um áudio explicando o processo de matrícula")
    assert "áudio" not in resultado.lower()
    assert "matrícula" in resultado


def test_remove_pedido_audio_sem_gatilho_retorna_igual():
    assert _remover_pedido_audio("qual o horário da biblioteca") == "qual o horário da biblioteca"


def test_remove_pedido_audio_so_gatilho_nao_fica_vazio():
    # Mensagem que É só o gatilho (raro, mas não pode virar string vazia
    # pro pipeline de RAG/síntese) — cai de volta pro texto original.
    resultado = _remover_pedido_audio("em áudio")
    assert resultado
