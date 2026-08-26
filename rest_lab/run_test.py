"""
Rodar de dentro da worktree `Oraculo-rest-mcp` (raiz do repo):

    python3 -m rest_lab.run_test

CLI de teste manual, sem WhatsApp/Celery/dispatcher — só para validar as
tools REST isoladamente (ver plano da sessão: teste via CLI local primeiro).
Não depende de `.env`/settings do Oráculo: as três APIs (JSONPlaceholder,
DummyJSON, httpbin) são públicas e sem autenticação. Todo comando exige o
prefixo "rest " (mesma regra usada quando plugado no WhatsApp via
`dispatcher_langgraph.py` — ver `rest_lab/router.py`). Digite 'rest ajuda'
pra ver os comandos reconhecidos, 'sair' pra encerrar.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from rest_lab.clients import fechar_todos
from rest_lab.router import rotear


async def main():
    print("=== rest_lab — laboratório REST (JSONPlaceholder / DummyJSON / httpbin) ===")
    print("Digite 'rest ajuda' para ver os comandos reconhecidos, 'sair' para encerrar.\n")

    try:
        while True:
            msg = input("Você: ").strip()
            if msg.lower() in ("sair", "exit", "quit"):
                break

            resultado = await rotear(msg)
            print(f"\n🤖 {resultado['mensagem']}\n")
    finally:
        await fechar_todos()


if __name__ == "__main__":
    asyncio.run(main())
