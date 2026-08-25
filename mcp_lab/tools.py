"""
mcp_lab/tools.py
====================
Facade fino sobre `McpLabUseCase` (Fase 4 do plano de integração,
Decisão 03) — mantém exatamente o mesmo contrato de função que `router.py`/
`run_test.py` sempre consumiram, então nenhum dos dois precisou mudar uma
linha. A lógica de verdade (chamadas MCP/httpx, tratamento de erro, e o
envio de imagem via WhatsApp) mora em
`src/application/use_cases/mcp_lab_use_case.py` — antes desta fase, vivia
aqui direto, sem passar por nenhuma camada de Application (e
`buscar_imagem` instanciava `EvolutionAdapter` direto — corrigido junto,
ver ADR 0006).
"""
from __future__ import annotations

from src.application.use_cases.mcp_lab_use_case import McpLabUseCase

_use_case = McpLabUseCase()


async def buscar_perguntas(query: str, site: str = "stackoverflow") -> dict:
    return await _use_case.buscar_perguntas(query, site)


async def obter_respostas(question_id: int, site: str = "stackoverflow") -> dict:
    return await _use_case.obter_respostas(question_id, site)


async def buscar_web(query: str) -> dict:
    return await _use_case.buscar_web(query)


async def buscar_imagem(query: str, chat_id: str) -> dict:
    return await _use_case.buscar_imagem(query, chat_id)


async def buscar_repos_github(query: str) -> dict:
    return await _use_case.buscar_repos_github(query)


async def perfil_github(usuario: str) -> dict:
    return await _use_case.perfil_github(usuario)
