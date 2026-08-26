"""
Rodar de dentro da worktree `Oraculo-rest-mcp` (raiz do repo):

    python3 -m mcp_lab.run_test

CLI de teste manual, sem WhatsApp/Celery/dispatcher — só para validar a
sessão MCP contra o servidor real do pipeworx isoladamente (ver plano da
sessão: teste via CLI local primeiro, isola problema de rede/protocolo de
problema de integração com o dispatcher). StackExchange é público (sem
chave); `stack site`/`stack imagem` (Brave) e `stack github`/`stack perfil`
(GitHub) precisam de `BRAVE_API_KEY`/`GITHUB_API_KEY` no `.env` do repo (lido
via `src.infrastructure.settings`, mesma settings do Oráculo).
Todo comando exige o prefixo "stack " (mesma regra usada quando plugado no
WhatsApp via `dispatcher_langgraph.py` — ver `mcp_lab/router.py`). Digite
'stack ajuda' pra ver os comandos reconhecidos, 'sair' pra encerrar.

`stack imagem <termo>` MANDA a imagem via Evolution API (efeito colateral) —
não dá pra testar o envio de verdade por aqui sem um `chat_id` real (grupo
homologado). Sem `--chat-id`, roda em modo "só busca": confirma que a
sessão MCP/API do Brave funciona, mas não tenta enviar nada (`chat_id`
vazio faz `tools.buscar_imagem` devolver erro amigável antes de chamar a
Evolution). Pra testar o envio de verdade, `python3 -m mcp_lab.run_test
--chat-id "120363409704662108@g.us"` (JID do grupo).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from mcp_lab.router import rotear


async def main():
    chat_id = ""
    if "--chat-id" in sys.argv:
        chat_id = sys.argv[sys.argv.index("--chat-id") + 1]

    print("=== mcp_lab — laboratório MCP (StackExchange / Brave / GitHub via pipeworx.io) ===")
    print("Digite 'stack ajuda' para ver os comandos reconhecidos, 'sair' para encerrar.")
    if not chat_id:
        print("(sem --chat-id: 'stack imagem' só valida a busca, não envia de verdade)\n")
    else:
        print(f"(--chat-id={chat_id!r}: 'stack imagem' vai mandar de verdade)\n")

    while True:
        msg = input("Você: ").strip()
        if msg.lower() in ("sair", "exit", "quit"):
            break

        resultado = await rotear(msg, chat_id=chat_id)
        print(f"\n🤖 {resultado['mensagem']}\n")


if __name__ == "__main__":
    asyncio.run(main())
