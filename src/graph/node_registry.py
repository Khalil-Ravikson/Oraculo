"""Registry central de nós — autodiscovery e registro."""

from typing import Dict, Callable, Optional, List, Type, Any
from src.graph.base_node import BaseNode, Port
import logging

logger = logging.getLogger(__name__)


class NodeRegistry:
    """
    Registry centralizado de nós do grafo.

    Funções:
    - Registro explícito de nós (via register())
    - Autodiscovery de nós em src/graph/nodes/
    - Validação de conexões (tipos de porta casam?)
    - Listagem pra Hub (/hub/graph-nodes)
    """

    def __init__(self):
        """Inicializa registry vazio."""
        self._nodes: Dict[str, BaseNode] = {}
        self._factories: Dict[str, Callable[..., BaseNode]] = {}

    def register(self, node: BaseNode) -> None:
        """
        Registra uma instância de nó.

        Args:
            node: Instância de BaseNode subclass.

        Raises:
            ValueError: Se node_id já registrado.
        """
        if node.node_id in self._nodes:
            raise ValueError(f"Node {node.node_id} já registrado")

        self._nodes[node.node_id] = node
        logger.debug(f"Node registered: {node.node_id} ({node.node_type})")

    def register_factory(
        self,
        node_type: str,
        factory: Callable[..., BaseNode]
    ) -> None:
        """
        Registra uma factory pra criar nós de um tipo.

        Usado quando há múltiplas instâncias do mesmo tipo
        (ex: múltiplos LLM providers com configs diferentes).

        Args:
            node_type: Tipo de nó (ex: 'llm_provider', 'parser').
            factory: Função que retorna BaseNode instance.
        """
        self._factories[node_type] = factory
        logger.debug(f"Factory registered: {node_type}")

    def get(self, node_id: str) -> Optional[BaseNode]:
        """
        Busca um nó registrado por ID.

        Args:
            node_id: Identificador único do nó.

        Returns:
            BaseNode instance ou None se não encontrado.
        """
        return self._nodes.get(node_id)

    def list_nodes(self) -> List[Dict[str, Any]]:
        """
        Lista todos os nós registrados com metadados.

        Usado pelo Hub pra renderizar /hub/graph-nodes.

        Returns:
            Lista de dicts com metadados do nó.
        """
        result = []
        for node in self._nodes.values():
            health = node.health_check
            result.append({
                "id": node.node_id,
                "type": node.node_type,
                "metadata": node.metadata,
                "health": {
                    "is_healthy": health.is_healthy,
                    "last_checked": health.last_checked,
                    "error": health.error_message
                } if health else None,
                "input_ports": [
                    {
                        "name": p.name,
                        "type": p.type_,
                        "required": p.required,
                        "description": p.description
                    }
                    for p in node.input_ports
                ],
                "output_ports": [
                    {
                        "name": p.name,
                        "type": p.type_,
                        "description": p.description
                    }
                    for p in node.output_ports
                ],
                "config_schema": node.config_schema
            })
        return sorted(result, key=lambda x: x["id"])

    def validate_connection(
        self,
        source_node_id: str,
        output_port_name: str,
        target_node_id: str,
        input_port_name: str
    ) -> tuple[bool, Optional[str]]:
        """
        Valida se uma conexão entre dois nós é permitida.

        Verifica:
        1. Ambos nós existem
        2. Ambas portas existem
        3. Tipos de porta casam

        Args:
            source_node_id: ID do nó source.
            output_port_name: Nome da porta de saída.
            target_node_id: ID do nó target.
            input_port_name: Nome da porta de entrada.

        Returns:
            (is_valid: bool, error_message: Optional[str])
        """
        source = self.get(source_node_id)
        target = self.get(target_node_id)

        if not source:
            return False, f"Source node not found: {source_node_id}"
        if not target:
            return False, f"Target node not found: {target_node_id}"

        source_output = next(
            (p for p in source.output_ports if p.name == output_port_name),
            None
        )
        target_input = next(
            (p for p in target.input_ports if p.name == input_port_name),
            None
        )

        if not source_output:
            return False, f"Output port not found: {source_node_id}.{output_port_name}"
        if not target_input:
            return False, f"Input port not found: {target_node_id}.{input_port_name}"

        # Validar tipos
        if str(source_output.type_) != str(target_input.type_):
            return False, (
                f"Type mismatch: "
                f"{source_node_id}.{output_port_name}({source_output.type_}) "
                f"→ "
                f"{target_node_id}.{input_port_name}({target_input.type_})"
            )

        return True, None

    def get_factory(self, node_type: str) -> Optional[Callable[..., BaseNode]]:
        """
        Busca uma factory registrada por tipo.

        Args:
            node_type: Tipo de nó (ex: 'llm_provider').

        Returns:
            Factory function ou None.
        """
        return self._factories.get(node_type)

    def count(self) -> int:
        """Retorna número de nós registrados."""
        return len(self._nodes)


# Singleton global
_global_registry: Optional[NodeRegistry] = None


def get_registry() -> NodeRegistry:
    """
    Retorna o registry global (lazy init com auto-discover).

    Returns:
        NodeRegistry singleton.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = NodeRegistry()
        _auto_register_known_nodes(_global_registry)
    return _global_registry


def _auto_register_known_nodes(registry: NodeRegistry) -> None:
    """
    Registra as instâncias padrão dos nós de `src/graph/nodes/`.

    Registro explícito (não `pkgutil` autodiscovery de verdade) — mesmo
    princípio de `bootstrap.py` (Camada 1, ver `plataforma_orientada_a_
    configuracao.md` §A): só existem 6 nós hoje, autodiscovery genérico
    seria abstração prematura. Adicionar um nó novo = 1 linha aqui.
    Falha em qualquer nó individual não derruba o registry inteiro (cada
    provider pode ter dependência opcional não instalada, ex.: kokoro).
    """
    from src.graph.nodes import (
        STTNode, TTSNode, EmbeddingsNode, LLMNode, ParserNode, ToolNode
    )

    for node_cls in (STTNode, TTSNode, EmbeddingsNode, LLMNode, ParserNode, ToolNode):
        try:
            registry.register(node_cls())
        except Exception as exc:
            logger.warning("Falha ao registrar nó %s: %s", node_cls.__name__, exc)


def reset_registry() -> None:
    """Limpa o registry (usado em testes)."""
    global _global_registry
    _global_registry = None
