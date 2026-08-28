"""
src/infrastructure/repositories/_optimistic.py
================================================================================
Peça compartilhada do controle de concorrência otimista (§N) usado pelos
repositórios de configuração/registro editáveis via Hub (`config_dinamica`,
`route_registry`, ...): cada linha tem uma coluna `versao`; o UPDATE inclui
`WHERE versao = :versao_esperada`; se afetar 0 linhas, outra escrita
aconteceu no meio e o endpoint devolve HTTP 409.
"""
from __future__ import annotations


class ConflitoDeVersao(Exception):
    """A `versao` esperada não bate com a do banco — outra escrita aconteceu
    entre o carregamento da tela e o salvamento."""

    def __init__(self, chave: str, esperada: int | None, atual: int | None) -> None:
        super().__init__(
            f"Conflito de versão para '{chave}': tela tinha v{esperada}, banco está em v{atual}."
        )
        self.chave = chave
        self.esperada = esperada
        self.atual = atual
