"""
rest_lab/tools.py
=====================
Facade fino sobre `RestLabUseCase` (Fase 3 do plano de integração,
Decisão 03) — mantém exatamente o mesmo contrato de função que `router.py`/
`run_test.py` sempre consumiram (`async def <op>(...) -> dict`, sempre
`{"mensagem": str}`, nunca levanta exceção), então nenhum dos dois precisou
mudar uma linha. A lógica de verdade (chamadas httpx, tratamento de erro)
mora em `src/application/use_cases/rest_lab_use_case.py` — antes desta
fase, vivia aqui direto, sem passar por nenhuma camada de Application.
"""
from __future__ import annotations

from src.application.use_cases.rest_lab_use_case import RestLabUseCase

_use_case = RestLabUseCase()


async def listar_usuarios() -> dict:
    return await _use_case.listar_usuarios()


async def obter_usuario(user_id: int) -> dict:
    return await _use_case.obter_usuario(user_id)


async def criar_post(title: str, body: str) -> dict:
    return await _use_case.criar_post(title, body)


async def atualizar_post(post_id: int, title: str) -> dict:
    return await _use_case.atualizar_post(post_id, title)


async def deletar_post(post_id: int) -> dict:
    return await _use_case.deletar_post(post_id)


async def listar_produtos(limit: int = 10) -> dict:
    return await _use_case.listar_produtos(limit)


async def buscar_produto(nome: str) -> dict:
    return await _use_case.buscar_produto(nome)


async def testar_status(code: int) -> dict:
    return await _use_case.testar_status(code)


async def echo_request() -> dict:
    return await _use_case.echo_request()
