"""
Rodar de dentro da worktree `Oraculo-langgraph` (raiz do repo), com a venv
local ativa:

    ./venv/Scripts/python.exe -m langgraph_experiment.run_test

Simula uma conversa multi-turno (igual WhatsApp) contra o grafo LangGraph,
reaproveitando Redis/Postgres/Gemini já configurados no .env (os mesmos
containers do `main` — não sobe Docker novo, ver decisão registrada na
conversa). Testa dois casos:
  1. "quero saber o calendário acadêmico 2026" -> rota RAG (sem interrupt)
  2. "quero abrir um chamado" -> rota ticket (3-4 interrupts, HITL multi-turn)
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Em produção o .env vira variável de ambiente real do container Docker
# (docker-compose `env_file:`), então o resto do código nunca precisou de
# load_dotenv(). Aqui, fora do Docker, precisamos carregar manualmente —
# senão libs como GoogleGenerativeAIEmbeddings (que leem GOOGLE_API_KEY/
# GEMINI_API_KEY direto de os.environ) não acham a chave.
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from langgraph.types import Command

from langgraph_experiment.graph import build_graph


async def _run_turn(app, config, payload):
    result = await app.ainvoke(payload, config=config)
    interrupts = result.get("__interrupt__")
    if interrupts:
        pergunta = interrupts[0].value.get("question", "(sem pergunta)")
        print(f"\n🤖 [PAUSADO] {pergunta}")
        return None
    print(f"\n🤖 [FINAL] {result.get('answer')}")
    return result


async def main():
    app = build_graph()
    session_id = f"teste_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": session_id}}

    print(f"=== Sessão {session_id} ===")
    print("Digite uma mensagem (ex: 'quero abrir um chamado' ou uma pergunta do RAG). 'sair' para encerrar.\n")

    first = True
    while True:
        msg = input("Você: ").strip()
        if msg.lower() in ("sair", "exit", "quit"):
            break

        if first:
            payload = {"session_id": session_id, "message": msg}
            first = False
        else:
            payload = Command(resume=msg)

        result = await _run_turn(app, config, payload)
        if result is not None:
            # turno fechado (RAG respondeu, ou ticket confirmado/cancelado) — nova sessão
            session_id = f"teste_{uuid.uuid4().hex[:8]}"
            config = {"configurable": {"thread_id": session_id}}
            first = True
            print(f"\n(nova sessão: {session_id})\n")


if __name__ == "__main__":
    asyncio.run(main())
