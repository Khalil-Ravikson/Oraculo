from __future__ import annotations

import re

from langgraph.types import interrupt

from langgraph_experiment.state import OraculoState

# Mesmo nível de heurística do L1 (regex) do Supervisor real
# (src/router/supervisor.py) — versão reduzida só para rotear entre os dois
# nodes deste experimento, não uma réplica das 5 camadas.
_RE_TICKET = re.compile(
    r"\b(ticket|chamado|abrir\s+chamado|problema\s+t[eé]cnico|suporte\s+t[eé]cnico)\b",
    re.I,
)
_RE_CRUD = re.compile(
    r"\b(atualizar|mudar|trocar|corrigir)\s+(meu|minha)?\s*(setor|centro|telefone|n[uú]mero|cadastro)\b",
    re.I,
)


def classify_node(state: OraculoState) -> dict:
    # Se quem chamou o grafo já decidiu a rota (ex: dispatcher_langgraph.py,
    # que reaproveita o Supervisor real), respeita — só cai no regex quando
    # invocado direto (ex: run_test.py, teste manual via CLI).
    if state.route in ("rag", "ticket", "crud"):
        return {}
    if _RE_CRUD.search(state.message):
        return {"route": "crud"}
    route = "ticket" if _RE_TICKET.search(state.message) else "rag"
    return {"route": route}


async def responder_rag_direto(mensagem: str) -> str:
    """Chama RAGSearchService/SynthesisService reais e devolve só o texto da
    resposta — extraído de rag_node pra ser reaproveitado também pelo filtro
    de "detour" institucional em dispatcher_langgraph.py (sem duplicar a
    lógica de busca+síntese)."""
    from src.agents.academic_knowledge.service import RAGSearchService
    from src.agents.academic_knowledge.synthesis import SynthesisService

    rag = RAGSearchService()
    result = await rag.buscar(mensagem)
    if not result.ok or not result.data.get("found"):
        return result.message or "Não encontrei informações sobre isso nos documentos da UEMA."

    synth = SynthesisService()
    synth_result = await synth.sintetizar(
        chunks=result.data.get("chunks", []),
        plan_ctx={"query": mensagem},
    )
    return synth_result.answer if synth_result.ok else f"[erro synthesis] {synth_result.error}"


async def rag_node(state: OraculoState) -> dict:
    """Reaproveita RAGSearchService/SynthesisService reais — nenhuma lógica
    de busca/síntese duplicada, só o orquestrador (LangGraph) muda."""
    return {"answer": await responder_rag_direto(state.message)}


# ─────────────────────────────────────────────────────────────────────────────
# Validadores puros (sem interrupt(), sem side effect) — usados DENTRO de cada
# node (pra decidir avançar ou re-perguntar) E por
# dispatcher_langgraph.py::VALIDATORS_POR_NODE (pra decidir, ANTES de resumir
# o grafo, se a mensagem do usuário parece resposta válida pro passo pendente
# ou um "detour" institucional). Um único lugar de verdade pra cada regra —
# evita a mesma regra divergir entre o node e o filtro de detour.
#
# Cada validador devolve (ok: bool, valor_normalizado). `ok=False` sinaliza
# tanto "resposta inválida" quanto candidato a detour — quem decide o que
# fazer com isso é o chamador (node re-pergunta; dispatcher tenta RAG antes
# de desistir).
# ─────────────────────────────────────────────────────────────────────────────

_RE_TIPO_INCIDENTE = re.compile(r"\bincidente\b|\bparou\b|\bquebrou\b|\bquebrad[oa]\b|\bpifou\b", re.I)
_RE_TIPO_REQUISICAO = re.compile(r"\brequisi[cç][ãa]o\b|\bpedido\b|\bsolicita[cç][ãa]o\b|\bnovo\b", re.I)


def validar_tipo(texto: str) -> tuple[bool, str | None]:
    t = texto.strip()
    if t == "1":
        return True, "Incidente"
    if t == "2":
        return True, "Requisicao"
    if _RE_TIPO_INCIDENTE.search(t):
        return True, "Incidente"
    if _RE_TIPO_REQUISICAO.search(t):
        return True, "Requisicao"
    return False, None


_CATEGORIA_SINONIMOS: dict[int, re.Pattern] = {
    1: re.compile(r"\bwi-?fi\b|\brede\b|\binternet\b|\bvpn\b|\bcabo\b|\bconectividade\b", re.I),
    2: re.compile(r"\bhardware\b|\bcomputador\b|\bpc\b|\bimpressora\b|\bperif[ée]rico\b|\bnotebook\b", re.I),
    3: re.compile(r"\bsigaa\b|\bsoftware\b|\bsistem[a]\b|\be-?mail\b|\bemail\b", re.I),
    4: re.compile(r"\bsenha\b|\blogin\b|\bacesso\b|\bconta\b|\bpermiss[ãa]o\b", re.I),
    5: re.compile(r"\btelefonia\b|\bramal\b|\btelefone\b", re.I),
    6: re.compile(r"\bpredial\b|\bel[ée]trica\b|\bmobili[áa]rio\b|\binfraestrutura\b", re.I),
}


def validar_categoria(texto: str) -> tuple[bool, str | None]:
    from src.agents.tickets.ticket_flow import SEED_CATEGORIAS

    categoria_por_id = {c["id"]: c["nome"] for c in SEED_CATEGORIAS}
    t = texto.strip()
    if t.isdigit() and int(t) in categoria_por_id:
        return True, categoria_por_id[int(t)]
    for cid, regex in _CATEGORIA_SINONIMOS.items():
        if regex.search(t):
            return True, categoria_por_id[cid]
    return False, None


def validar_queixa(texto: str) -> tuple[bool, str | None]:
    t = texto.strip()
    if len(t) >= 3:
        return True, t
    return False, None


_RE_CONFIRMA = re.compile(
    r"\bsim\b|\bconfirmo\b|\bpode\s+enviar\b|\bpode\s+mandar\b|\bmanda\b|\bok\b|\bisso\s+mesmo\b|\bconfirmad[oa]\b",
    re.I,
)
_RE_NEGA = re.compile(
    r"\bn[ãa]o\b|\bcancela\b|\bcancelar\b|\bdeixa\s+pra\s+l[áa]\b|\bdesist[oi]\b|\besquece\b",
    re.I,
)


def validar_confirmacao(texto: str) -> tuple[bool, bool | None]:
    t = texto.strip().lower()
    if t == "s":
        return True, True
    if t == "n":
        return True, False
    if _RE_NEGA.search(t):
        return True, False
    if _RE_CONFIRMA.search(t):
        return True, True
    return False, None


_RE_CAMPO_TELEFONE = re.compile(r"\btelefone\b|\bn[uú]mero\b|\bcelular\b|\bfone\b", re.I)
_RE_CAMPO_SETOR = re.compile(r"\bsetor\b|\bcentro\b", re.I)


def validar_campo_crud(texto: str) -> tuple[bool, str | None]:
    t = texto.strip()
    if _RE_CAMPO_TELEFONE.search(t):
        return True, "telefone"
    if _RE_CAMPO_SETOR.search(t):
        return True, "setor"
    return False, None


def validar_valor_crud(campo: str, texto: str) -> tuple[bool, str | None]:
    t = texto.strip()
    if campo == "setor":
        from src.domain.entities.enums import CentroEnum

        t_upper = t.upper()
        for membro in CentroEnum:
            if t_upper == membro.value:
                return True, membro.value
        return False, None
    if campo == "telefone":
        digitos = re.sub(r"\D", "", t)
        if 8 <= len(digitos) <= 13:
            return True, digitos
        return False, None
    return False, None


def _com_erro(pergunta: str, erro: str) -> str:
    return f"{erro}\n\n{pergunta}" if erro else pergunta


# ─────────────────────────────────────────────────────────────────────────────
# Saída explícita do HITL — comando reconhecido em QUALQUER pergunta do funil
# (ticket ou CRUD), checado ANTES do validador específico do node. Existe
# porque, sem isso, uma mensagem como "sair"/"cancelar" solta no meio do
# funil não validava pro passo pendente e caía no filtro de detour
# institucional (dispatcher_langgraph.py) — ia pro RAG, respondia "não
# encontrei" e voltava a repetir a mesma pergunta pendente, sem nunca sair.
# Ver _eh_saida() usado também por dispatcher_langgraph.py::processar() pra
# pular o detour quando a mensagem é claramente um pedido de saída.
# ─────────────────────────────────────────────────────────────────────────────

_RE_SAIR_HITL = re.compile(r"^\s*(sair|cancelar?|desist[oi]r?|abortar|encerrar|parar)\s*[.!]?\s*$", re.I)
_MSG_SAIDA_HITL = "🚪 Você saiu do atendimento. Se precisar, é só chamar de novo."


def _eh_saida(texto: str) -> bool:
    return bool(_RE_SAIR_HITL.match(texto.strip()))


def _resultado_saida() -> dict:
    return {"cancelado": True, "answer": _MSG_SAIDA_HITL}


# ─────────────────────────────────────────────────────────────────────────────
# Funil de ticket — 1 interrupt() por node (não 4 empilhados no mesmo node).
#
# Motivo (ver plano/investigação): múltiplos interrupt() sequenciais no mesmo
# node é um padrão documentado como válido pelo LangGraph, mas a implementação
# real do checkpointer usado aqui (AsyncRedisSaver, pacote
# langgraph-checkpoint-redis) tem bugs abertos conhecidos especificamente na
# resumption de múltiplos interrupts pendentes:
#   - https://github.com/langchain-ai/langgraph/issues/5074
#   - https://github.com/redis-developer/langgraph-redis/issues/133
# Reproduzido nesta branch: funil de 4 interrupts no mesmo node funcionava no
# 1º resume e quebrava no 2º (o `aget_state().next` voltava vazio como se o
# grafo tivesse terminado). Quebrar em 1 node por pergunta reduz a
# dependência à trilha mais simples/testada do pacote (exatamente 1 interrupt
# pendente por vez) sem trocar de checkpointer.
# ─────────────────────────────────────────────────────────────────────────────


async def ticket_ask_tipo(state: OraculoState) -> dict:
    # RBAC — mesma checagem do fluxo real (ticket_flow.py), portada pra cá.
    # Roda no topo do node de ENTRADA do funil: LangGraph reexecuta o corpo
    # do node do início a cada resume, então isso é rechecado a cada turno
    # (leitura pura, sem side effect — idempotente, ok repetir).
    from src.agents.tickets.rbac import checar_permissao_chamado

    autorizado, msg_bloqueio, _ = await checar_permissao_chamado(state.session_id)
    if not autorizado:
        return {"cancelado": True, "answer": msg_bloqueio}

    pergunta = _com_erro(
        "É um *Incidente* (algo parou) ou uma *Requisição* (pedido novo)? "
        "Responda 1 ou 2, ou diga com suas palavras.",
        state.ticket_error,
    )
    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_tipo(str(resposta))
    if ok:
        return {"ticket_data": {**state.ticket_data, "tipo": valor}, "ticket_error": ""}
    return {"ticket_error": "❌ Não entendi — responda 1 (Incidente) ou 2 (Requisição), ou diga com suas palavras."}


def _tipo_valido(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    return "ticket_ask_categoria" if state.ticket_data.get("tipo") and not state.ticket_error else "ticket_ask_tipo"


async def ticket_ask_categoria(state: OraculoState) -> dict:
    from src.agents.tickets.ticket_flow import SEED_CATEGORIAS

    lista = "\n".join(f"{c['id']}. {c['nome']}" for c in SEED_CATEGORIAS)
    pergunta = _com_erro(
        f"Qual categoria melhor descreve o problema?\n{lista}\n(pode responder pelo número ou com suas palavras)",
        state.ticket_error,
    )
    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_categoria(str(resposta))
    if ok:
        return {"ticket_data": {**state.ticket_data, "categoria": valor}, "ticket_error": ""}
    return {"ticket_error": "❌ Não reconheci a categoria — escolha um número da lista acima ou descreva melhor."}


def _categoria_valida(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    return "ticket_ask_queixa" if state.ticket_data.get("categoria") and not state.ticket_error else "ticket_ask_categoria"


async def ticket_ask_queixa(state: OraculoState) -> dict:
    pergunta = _com_erro("Descreva o problema ou pedido com suas palavras:", state.ticket_error)
    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_queixa(str(resposta))
    if ok:
        return {"ticket_data": {**state.ticket_data, "queixa": valor}, "ticket_error": ""}
    return {"ticket_error": "❌ Descreva com um pouco mais de detalhe (mínimo 3 caracteres)."}


def _queixa_valida(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    return "ticket_confirm" if state.ticket_data.get("queixa") and not state.ticket_error else "ticket_ask_queixa"


async def ticket_confirm(state: OraculoState) -> dict:
    d = state.ticket_data
    resumo = f"Tipo: {d.get('tipo')}\nCategoria: {d.get('categoria')}\nDescrição: {d.get('queixa')}"
    pergunta = _com_erro(f"{resumo}\n\nConfirma o envio? (sim/não)", state.ticket_error)
    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_confirmacao(str(resposta))
    if not ok:
        return {"ticket_confirmed": None, "ticket_error": "❌ Não entendi — responda algo como \"sim\"/\"pode enviar\" ou \"não\"/\"cancelar\"."}
    if valor:
        return {"ticket_confirmed": True, "ticket_error": ""}
    return {"ticket_confirmed": False, "ticket_error": "", "answer": "❌ Ticket cancelado."}


def _confirm_route(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    if state.ticket_confirmed is True:
        return "ticket_save"
    if state.ticket_confirmed is False:
        return "__end__"
    return "ticket_confirm"


async def ticket_save(state: OraculoState) -> dict:
    """Só efeito colateral (grava o ticket) — separado do node de confirmação
    (ticket_confirm) pra manter idempotência: se o checkpoint falhar depois
    daqui, o próximo resume não repete a pergunta de confirmação."""
    from src.capabilities.persistence.dev_dump import salvar_json_dev

    d = state.ticket_data
    resumo = f"Tipo: {d.get('tipo')}\nCategoria: {d.get('categoria')}\nDescrição: {d.get('queixa')}"
    caminho = salvar_json_dev("tickets_dev_langgraph", state.session_id, d)
    return {"answer": f"✅ Ticket de teste registrado (LangGraph)! Salvo em {caminho}\n\n{resumo}"}


# ─────────────────────────────────────────────────────────────────────────────
# Funil de CRUD de cadastro — mesmo padrão do ticket (1 interrupt por node,
# validação com re-pergunta, save separado do confirm). Escopo igual ao
# crud_tool.py original (src/agents/tickets/crud_tool.py): só setor/telefone.
# Reaproveita a mesma função de escrita real
# (ticket_repository.atualizar_setor_e_telefone) e o mesmo gate
# settings.DEV_TEST_NO_DB_WRITE — nenhuma lógica de persistência nova.
# ─────────────────────────────────────────────────────────────────────────────


async def crud_ask_campo(state: OraculoState) -> dict:
    # RBAC — mesma checagem do fluxo real (crud_tool.py), portada pra cá.
    # Mesmo motivo/idempotência do ticket_ask_tipo (ver comentário lá).
    from src.agents.tickets.rbac import checar_permissao_chamado

    autorizado, msg_bloqueio, _ = await checar_permissao_chamado(state.session_id)
    if not autorizado:
        return {"cancelado": True, "answer": msg_bloqueio}

    pergunta = _com_erro(
        "O que você quer atualizar: seu *setor* ou seu *telefone*? (responda 'setor' ou 'telefone')",
        state.crud_error,
    )
    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_campo_crud(str(resposta))
    if ok:
        return {"crud_data": {**state.crud_data, "campo": valor}, "crud_error": ""}
    return {"crud_error": "❌ Não entendi — responda 'setor' ou 'telefone'."}


def _campo_crud_valido(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    return "crud_ask_valor" if state.crud_data.get("campo") and not state.crud_error else "crud_ask_campo"


async def crud_ask_valor(state: OraculoState) -> dict:
    from src.domain.entities.enums import CentroEnum

    campo = state.crud_data.get("campo")
    if campo == "setor":
        siglas = ", ".join(m.value for m in CentroEnum)
        pergunta = _com_erro(f"Qual é o novo setor? Opções: {siglas}", state.crud_error)
    else:
        pergunta = _com_erro("Qual é o novo número de telefone? (com DDD)", state.crud_error)

    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_valor_crud(campo, str(resposta))
    if ok:
        return {"crud_data": {**state.crud_data, "valor": valor}, "crud_error": ""}
    if campo == "setor":
        return {"crud_error": "❌ Setor inválido — responda com uma das siglas da lista acima."}
    return {"crud_error": "❌ Telefone inválido — informe DDD + número (só dígitos)."}


def _valor_crud_valido(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    return "crud_confirm" if state.crud_data.get("valor") and not state.crud_error else "crud_ask_valor"


async def crud_confirm(state: OraculoState) -> dict:
    d = state.crud_data
    resumo = f"Campo: {d.get('campo')}\nNovo valor: {d.get('valor')}"
    pergunta = _com_erro(f"{resumo}\n\nConfirma a atualização? (sim/não)", state.crud_error)
    resposta = interrupt({"question": pergunta})
    if _eh_saida(str(resposta)):
        return _resultado_saida()
    ok, valor = validar_confirmacao(str(resposta))
    if not ok:
        return {"crud_confirmed": None, "crud_error": "❌ Não entendi — responda algo como \"sim\"/\"pode enviar\" ou \"não\"/\"cancelar\"."}
    if valor:
        return {"crud_confirmed": True, "crud_error": ""}
    return {"crud_confirmed": False, "crud_error": "", "answer": "❌ Atualização cancelada."}


def _crud_confirm_route(state: OraculoState) -> str:
    if state.cancelado:
        return "__end__"
    if state.crud_confirmed is True:
        return "crud_save"
    if state.crud_confirmed is False:
        return "__end__"
    return "crud_confirm"


async def crud_save(state: OraculoState) -> dict:
    from src.infrastructure.settings import settings

    d = state.crud_data
    campo, valor = d.get("campo"), d.get("valor")
    resumo = f"Campo: {campo}\nNovo valor: {valor}"

    if settings.DEV_TEST_NO_DB_WRITE:
        from src.capabilities.persistence.dev_dump import salvar_json_dev

        caminho = salvar_json_dev("crud_dev_langgraph", state.session_id, d)
        return {"answer": f"✅ [DEV] Atualização registrada (sem tocar o banco)! Salvo em {caminho}\n\n{resumo}"}

    from src.capabilities.persistence.ticket_repository import atualizar_setor_e_telefone

    kwargs = {"novo_centro": valor} if campo == "setor" else {"novo_telefone": valor}
    await atualizar_setor_e_telefone(telefone_atual=state.session_id, **kwargs)
    return {"answer": f"✅ Cadastro atualizado com sucesso!\n\n{resumo}"}


# Registry usado por dispatcher_langgraph.py pro filtro de "detour": dado o
# node pendente (state.next[0]), decide se a mensagem do usuário parece
# resposta válida pra ELE, sem duplicar a regra de validação de cada node.
# `state_values` aqui é um dict (StateSnapshot.values, vindo de
# app.aget_state() no dispatcher) — não a instância Pydantic OraculoState
# usada dentro dos nodes/edges do grafo.
VALIDATORS_POR_NODE = {
    "ticket_ask_tipo": lambda state_values, texto: validar_tipo(texto)[0],
    "ticket_ask_categoria": lambda state_values, texto: validar_categoria(texto)[0],
    "ticket_ask_queixa": lambda state_values, texto: validar_queixa(texto)[0],
    "ticket_confirm": lambda state_values, texto: validar_confirmacao(texto)[0],
    "crud_ask_campo": lambda state_values, texto: validar_campo_crud(texto)[0],
    "crud_ask_valor": lambda state_values, texto: validar_valor_crud(
        (state_values.get("crud_data") or {}).get("campo", ""), texto
    )[0],
    "crud_confirm": lambda state_values, texto: validar_confirmacao(texto)[0],
}
