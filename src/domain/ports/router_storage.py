from typing import Protocol, TypedDict

# 1. Definimos o formato da resposta que esperamos
class ResultadoBuscaRouter(TypedDict):
    tool_name: str
    query_exemplo: str
    score: float

# 2. Definimos o contrato (A Vaga de Emprego)
class IRouterStorage(Protocol):
    """
    Contrato para o banco de dados vetorial do roteador (agnóstico de provedor).
    Aqui NÃO vai código de verdade, apenas os três pontinhos (...).
    """
    
    async def registrar_queries_tool_async(self, tool_name: str, queries: list[str]) -> int:
        """Regista uma lista de queries de exemplo para uma tool. Retorna qtd registada."""
        ...

    async def buscar_tool_semelhante_async(self, texto: str, limit: int = 3) -> list[ResultadoBuscaRouter]:
        """Busca as tools mais próximas semanticamente ao texto dado."""
        ...
    
    async def verificar_indice_vazio_async(self) -> bool:
        """Verifica se existem dados registados no banco."""
        ...