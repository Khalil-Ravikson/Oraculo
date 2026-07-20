# tests/unit/router/test_gatekeeper.py
"""
Testes de src/router/gatekeeper.py::MessageRouter.route() — sem cobertura
anterior (achado durante teste manual real via docker-compose, Sprint 3).

Bug corrigido aqui: o método tinha blocos DUPLICADOS de "trava beta" e de
"gatilho no grupo" (comentários "0." e "1." repetidos), e a primeira
ocorrência do gatilho de grupo ($/!/@oraculo) retornava IGNORE ANTES da
checagem de "Funil de Cadastro (Máxima Prioridade)". Como
`_handle_message` (process_message_task.py) converte todo IGNORE em LLM,
qualquer mensagem de texto livre sem esses gatilhos — mesmo de quem nunca
se cadastrou — ia direto para o RAG, nunca caindo no funil de cadastro.
"""
from src.router.gatekeeper import DispatchTarget, MessageRouter

ALLOWED_GROUP = "120363409704662108@g.us"


def _route(router, **overrides):
    defaults = dict(
        text="quero me cadastrar",
        sender_jid="5599999990001@s.whatsapp.net",
        is_group=True,
        is_admin=False,
        is_registered=False,
        in_register_mode=False,
        allowed_group_jid=ALLOWED_GROUP,
        remote_jid=ALLOWED_GROUP,
    )
    defaults.update(overrides)
    return router.route(**defaults)


def test_nao_registrado_no_grupo_sem_trigger_cai_no_funil_de_cadastro():
    """Regressão do bug: texto livre sem $/!/@oraculo, de quem não está
    registrado, deve priorizar o funil de cadastro sobre o gatilho de grupo."""
    router = MessageRouter()
    decision = _route(router, text="quero me cadastrar", is_registered=False)

    assert decision.target == DispatchTarget.REGISTER_MODE


def test_em_modo_de_registro_qualquer_texto_permanece_no_funil():
    router = MessageRouter()
    decision = _route(router, text="João Da Silva", in_register_mode=True, is_registered=False)

    assert decision.target == DispatchTarget.REGISTER_MODE


def test_registrado_no_grupo_sem_trigger_e_ignorado():
    """Depois de registrado, o gatilho de grupo volta a valer normalmente."""
    router = MessageRouter()
    decision = _route(router, text="oi, tudo bem?", is_registered=True)

    assert decision.target == DispatchTarget.IGNORE
    assert decision.reason == "sem_trigger_grupo"


def test_registrado_no_grupo_com_mencao_vai_para_llm():
    router = MessageRouter()
    decision = _route(router, text="@oraculo qual o calendário?", is_registered=True)

    assert decision.target == DispatchTarget.LLM


def test_grupo_nao_homologado_e_sempre_ignorado_mesmo_sem_registro():
    router = MessageRouter()
    decision = _route(
        router, text="quero me cadastrar", is_registered=False,
        remote_jid="999999@g.us",
    )

    assert decision.target == DispatchTarget.IGNORE
    assert decision.reason == "grupo_estranho_ignorado"


def test_privado_nao_admin_e_sempre_ignorado_mesmo_sem_registro():
    router = MessageRouter()
    decision = _route(
        router, text="quero me cadastrar", is_registered=False,
        is_group=False, is_admin=False,
    )

    assert decision.target == DispatchTarget.IGNORE
    assert decision.reason == "privado_bloqueado_no_beta"
