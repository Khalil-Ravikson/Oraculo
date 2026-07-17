import asyncio
from src.application.routing.semantic_router import rotear
from src.application.chain.planner import criar_plano

async def main():
    decision = await rotear('https://www.youtube.com/watch?v=dQw4w9WgXcQ', '123', {})
    plan = await criar_plano('https://www.youtube.com/watch?v=dQw4w9WgXcQ', '123', decision.rota, decision.dag_hint, {})
    print(plan)

asyncio.run(main())
