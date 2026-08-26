"""
rest_lab/router.py
======================
Roteamento determinístico por regex — mesmo nível de heurística de
`langgraph_experiment/nodes.py::_RE_TICKET`/`_RE_CRUD`. Decisão explícita
desta rodada: o LLM NÃO decide qual tool chamar (ver plano da sessão) —
isso fica pra uma evolução futura com function-calling nativo do Gemini,
que hoje não existe em lugar nenhum do Oráculo (achado desta exploração:
`bind_tools` do LangChain em `src/capabilities/messaging/gmail_tool.py` é
código morto, sem consumidor).

Todo comando exige o prefixo `rest` (ex: "rest listar usuários") — esse
laboratório roda plugado no fluxo real do WhatsApp
(`src/application/runtime/dispatcher_langgraph.py`), então o prefixo evita
qualquer chance de colidir com uma pergunta acadêmica real do aluno
("usuário 3" sem prefixo NÃO é interceptado; "rest usuário 3" é).

Duas funções públicas:
  - `tentar_rotear(mensagem) -> dict | None`: usada pelo dispatcher — devolve
    `None` (não intercepta) se a mensagem não começa com "rest ", deixando o
    fluxo normal do Oráculo seguir.
  - `rotear(mensagem) -> dict`: usada pela CLI (`run_test.py`) — mesma coisa,
    mas nunca devolve `None` (cai no texto de ajuda se não reconhecer).
"""
from __future__ import annotations

import re

from rest_lab import tools

_AJUDA = (
    "🤖 Comandos reconhecidos (laboratório REST):\n"
    "- rest listar usuários\n"
    "- rest usuário <id>\n"
    "- rest criar post <título> | <corpo>\n"
    "- rest atualizar post <id> | <título novo>\n"
    "- rest deletar post <id>\n"
    "- rest listar produtos\n"
    "- rest buscar produto <nome>\n"
    "- rest testar status <code>\n"
    "- rest echo\n"
    "- rest ajuda"
)

_PREFIXO = r"^\s*rest\s+"

_RE_AJUDA = re.compile(r"^\s*rest\s*(ajuda)?\s*$", re.I)
_RE_LISTAR_USUARIOS = re.compile(_PREFIXO + r"listar?\s+usu[áa]rios?\s*$", re.I)
_RE_USUARIO_ID = re.compile(_PREFIXO + r"usu[áa]rio\s+(\d+)\s*$", re.I)
_RE_CRIAR_POST = re.compile(_PREFIXO + r"criar\s+post\s+(.+?)\s*\|\s*(.+?)\s*$", re.I)
_RE_ATUALIZAR_POST = re.compile(_PREFIXO + r"atualizar\s+post\s+(\d+)\s*\|\s*(.+?)\s*$", re.I)
_RE_DELETAR_POST = re.compile(_PREFIXO + r"deletar\s+post\s+(\d+)\s*$", re.I)
_RE_LISTAR_PRODUTOS = re.compile(_PREFIXO + r"listar?\s+produtos?\s*$", re.I)
_RE_BUSCAR_PRODUTO = re.compile(_PREFIXO + r"buscar\s+produto\s+(.+?)\s*$", re.I)
_RE_TESTAR_STATUS = re.compile(_PREFIXO + r"testar\s+status\s+(\d+)\s*$", re.I)
_RE_ECHO = re.compile(_PREFIXO + r"echo\s*$", re.I)


async def tentar_rotear(mensagem: str) -> dict | None:
    if not re.match(r"^\s*rest\b", mensagem, re.I):
        return None  # fast-path: nem é comando deste laboratório

    if _RE_LISTAR_USUARIOS.match(mensagem):
        return await tools.listar_usuarios()

    if m := _RE_USUARIO_ID.match(mensagem):
        return await tools.obter_usuario(int(m.group(1)))

    if m := _RE_CRIAR_POST.match(mensagem):
        return await tools.criar_post(title=m.group(1), body=m.group(2))

    if m := _RE_ATUALIZAR_POST.match(mensagem):
        return await tools.atualizar_post(post_id=int(m.group(1)), title=m.group(2))

    if m := _RE_DELETAR_POST.match(mensagem):
        return await tools.deletar_post(post_id=int(m.group(1)))

    if _RE_LISTAR_PRODUTOS.match(mensagem):
        return await tools.listar_produtos()

    if m := _RE_BUSCAR_PRODUTO.match(mensagem):
        return await tools.buscar_produto(nome=m.group(1))

    if m := _RE_TESTAR_STATUS.match(mensagem):
        return await tools.testar_status(int(m.group(1)))

    if _RE_ECHO.match(mensagem):
        return await tools.echo_request()

    if _RE_AJUDA.match(mensagem):
        return {"mensagem": _AJUDA}

    # Começou com "rest" mas não bateu em nenhum padrão conhecido.
    return {"mensagem": f"❓ Não reconheci esse comando.\n\n{_AJUDA}"}


async def rotear(mensagem: str) -> dict:
    resultado = await tentar_rotear(mensagem)
    return resultado if resultado is not None else {"mensagem": _AJUDA}
