import asyncio
import time

from langgraph_experiment.nodes import responder_rag_direto


async def uma_chamada(i: int):
    t0 = time.monotonic()
    try:
        resposta = await responder_rag_direto(
            f"pergunta de teste número {i}", rota="GERAL", session_id=f"carga-{i}",
        )
        ok = "lentidão" not in resposta.lower()  # essa frase = timeout/erro no dispatch
    except Exception as e:
        resposta, ok = f"EXCEÇÃO: {e}", False
    return i, ok, time.monotonic() - t0, resposta[:80]


async def main(n: int):
    resultados = await asyncio.gather(*[uma_chamada(i) for i in range(n)])
    falhas = 0
    for i, ok, dt, resposta in resultados:
        marca = "OK " if ok else "FALHA"
        falhas += 0 if ok else 1
        print(f"[{marca}] #{i:03d} {dt:5.2f}s  {resposta!r}")
    print(f"\n{n - falhas}/{n} concluíram sem timeout/exceção")


if __name__ == "__main__":
    import sys
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
