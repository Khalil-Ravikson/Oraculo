"""
mcp_lab/router.py
======================
Roteamento determinístico por regex — mesma decisão e mesmo motivo do
`rest_lab/router.py`: o LLM NÃO decide qual tool MCP chamar nesta rodada
(ver `notas.md` §10.1 — Oráculo não tem function-calling LLM funcionando em
produção hoje). Fica pra uma evolução futura com `google.genai`
function-calling, se algum dia decidido.

Dois prefixos, cada um cobrindo seu(s) servidor(es) — "stack" sozinho virou
ambíguo depois que o laboratório cresceu além do StackExchange (usuário
achou confuso "stack site"/"stack imagem" parecerem StackOverflow):
  - `stack ...`  → StackExchange (`buscar`/`respostas`) e GitHub
                   (`github`/`perfil`) — não tem "site"/"web" no nome, sem
                   ambiguidade com StackOverflow.
  - `brave ...`  → Brave Search (`site`/`imagem`) — nome do provedor deixa
                   explícito que não é sobre pilha/StackOverflow.
Esse laboratório roda plugado no fluxo real do WhatsApp
(`src/application/runtime/dispatcher_langgraph.py`), então os prefixos
evitam qualquer chance de colidir com uma pergunta acadêmica real do aluno.

Duas funções públicas, mesmo contrato do `rest_lab/router.py`:
  - `tentar_rotear(mensagem) -> dict | None`: usada pelo dispatcher — devolve
    `None` (não intercepta) se a mensagem não começa com "stack "/"brave ",
    deixando o fluxo normal do Oráculo seguir.
  - `rotear(mensagem) -> dict`: usada pela CLI (`run_test.py`) — mesma coisa,
    mas nunca devolve `None` (cai no texto de ajuda se não reconhecer).
"""
from __future__ import annotations

import re

from mcp_lab import tools

_AJUDA = (
    "🤖 Comandos reconhecidos (laboratório MCP):\n"
    "- stack buscar <termo>              (StackExchange)\n"
    "- stack respostas <id da pergunta>  (StackExchange)\n"
    "- stack github <termo>              (busca repositório)\n"
    "- stack perfil <usuário>            (perfil GitHub)\n"
    "- brave site <termo>                (busca web)\n"
    "- brave imagem <termo>              (busca + envia imagem)\n"
    "- stack ajuda / brave ajuda"
)

_PREFIXO_STACK = r"^\s*stack\s+"
_PREFIXO_BRAVE = r"^\s*brave\s+"

_RE_AJUDA = re.compile(r"^\s*(stack|brave)\s*(ajuda)?\s*$", re.I)
_RE_BUSCAR = re.compile(_PREFIXO_STACK + r"buscar\s+(.+?)\s*$", re.I)
_RE_RESPOSTAS = re.compile(_PREFIXO_STACK + r"respostas?\s+(\d+)\s*$", re.I)
_RE_GITHUB = re.compile(_PREFIXO_STACK + r"github\s+(.+?)\s*$", re.I)
_RE_PERFIL = re.compile(_PREFIXO_STACK + r"perfil\s+(\S+)\s*$", re.I)
_RE_SITE = re.compile(_PREFIXO_BRAVE + r"sites?\s+(.+?)\s*$", re.I)
_RE_IMAGEM = re.compile(_PREFIXO_BRAVE + r"(?:imagem|imagens)\s+(.+?)\s*$", re.I)


async def tentar_rotear(mensagem: str, chat_id: str = "") -> dict | None:
    if not re.match(r"^\s*(stack|brave)\b", mensagem, re.I):
        return None  # fast-path: nem é comando deste laboratório

    if m := _RE_BUSCAR.match(mensagem):
        return await tools.buscar_perguntas(query=m.group(1))

    if m := _RE_RESPOSTAS.match(mensagem):
        return await tools.obter_respostas(question_id=int(m.group(1)))

    if m := _RE_GITHUB.match(mensagem):
        return await tools.buscar_repos_github(query=m.group(1))

    if m := _RE_PERFIL.match(mensagem):
        return await tools.perfil_github(usuario=m.group(1))

    if m := _RE_SITE.match(mensagem):
        return await tools.buscar_web(query=m.group(1))

    if m := _RE_IMAGEM.match(mensagem):
        return await tools.buscar_imagem(query=m.group(1), chat_id=chat_id)

    if _RE_AJUDA.match(mensagem):
        return {"mensagem": _AJUDA}

    # Começou com "stack"/"brave" mas não bateu em nenhum padrão conhecido.
    return {"mensagem": f"❓ Não reconheci esse comando.\n\n{_AJUDA}"}


async def rotear(mensagem: str, chat_id: str = "") -> dict:
    resultado = await tentar_rotear(mensagem, chat_id=chat_id)
    return resultado if resultado is not None else {"mensagem": _AJUDA}
