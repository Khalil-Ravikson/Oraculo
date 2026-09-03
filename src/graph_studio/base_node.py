"""Abstração BaseNode — interface comum para todos os provedores."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Dict, List
from src.graph_studio.execution_context import ExecutionContext


class PortType(str, Enum):
    """
    Tipos de porta padrão (tipagem de entrada/saída).

    Usado pra validar conexões entre nós: output_type == input_type.
    """

    # Tipos principais
    LLM_RESPONSE = "llm_response"
    """Resposta de modelo de linguagem (texto estruturado/não-estruturado)."""

    EMBEDDINGS = "embeddings"
    """Vetor de embeddings (list[float])."""

    TOKENS = "tokens"
    """Contagem ou lista de tokens."""

    TEXT = "text"
    """Texto puro (string)."""

    STRUCTURED = "structured"
    """Dados estruturados (dict/JSON)."""

    AUDIO = "audio"
    """Dados de áudio (bytes ou URI)."""

    FILE = "file"
    """Arquivo (caminho ou stream)."""

    BOOLEAN = "boolean"
    """Booleano (true/false)."""

    NUMBER = "number"
    """Número (int ou float)."""

    ARRAY = "array"
    """Array/lista."""

    # Tipos customizados (adicione conforme necessário)
    CUSTOM = "custom"
    """Tipo customizado (specify em schema)."""


@dataclass
class Port:
    """
    Definição de porta de entrada/saída de um nó.

    Uma porta é um ponto de conexão tipado que permite dados fluir
    entre nós em um grafo.
    """

    name: str
    """Nome único da porta (ex: 'prompt', 'response', 'embeddings')."""

    type_: PortType | str
    """Tipo da porta (para validação de conexão)."""

    description: str
    """Descrição legível (para UI)."""

    required: bool = True
    """Se é obrigatória (entrada) ou sempre produzida (saída)."""

    schema: Optional[Dict[str, Any]] = None
    """
    JSON Schema opcional pra validação de dados mais rigorosa.
    Ex: {"type": "array", "items": {"type": "number"}}
    """

    def __post_init__(self):
        """Validação pós-init."""
        if not isinstance(self.type_, str) and not isinstance(self.type_, PortType):
            raise ValueError(f"Invalid type_: {self.type_}")


@dataclass
class NodeHealthStatus:
    """Status de saúde de um nó (circuit breaker, disponibilidade)."""

    is_healthy: bool
    """Se o nó está saudável."""

    last_checked: str
    """ISO8601 timestamp do último check."""

    error_message: Optional[str] = None
    """Mensagem de erro se não-saudável."""

    details: Optional[Dict[str, Any]] = None
    """Detalhes adicionais (latência, taxa de erro, etc.)."""


class BaseNode(ABC):
    """
    Abstração comum para todos os nós do grafo.

    Um nó é um componente executável que:
    - Recebe dados de entrada via portas tipadas
    - Executa processamento (chamada a API, modelo, ferramenta, etc.)
    - Produz dados de saída

    Exemplos:
    - LLMNode: chama modelo de IA (Gemini, etc.)
    - ParserNode: parseia PDF/Docx em chunks
    - ToolNode: executa ferramenta (email, calendar, etc.)
    - STTNode (futuro): transcreve áudio
    - ChannelNode (futuro): recebe mensagens de Telegram/Slack

    Uso:
        node = LLMNode(provider_name="gemini", model="gemini-2.0-pro")
        context = ExecutionContext(tenant_id="UEMA")
        result = await node.execute(
            {"prompt": "Olá, como você está?"},
            context
        )
        print(result["response"])
    """

    @property
    @abstractmethod
    def node_id(self) -> str:
        """
        Identificador único do nó.

        Convenção: {tipo}_{nome}
        Exemplos: 'llm_primary', 'rag_search', 'stt_whisper', 'tool_email'

        Returns:
            Identificador único (string lowercase com underscores).
        """
        pass

    @property
    @abstractmethod
    def node_type(self) -> str:
        """
        Tipo de nó (categoria, para UI/registry).

        Exemplos: 'llm_provider', 'stt_provider', 'parser', 'tool', 'agent'

        Returns:
            Tipo do nó (string).
        """
        pass

    @property
    @abstractmethod
    def input_ports(self) -> List[Port]:
        """
        Portas de entrada que este nó espera/aceita.

        Returns:
            Lista de Port (pode ser vazia se nó é "source").
        """
        pass

    @property
    @abstractmethod
    def output_ports(self) -> List[Port]:
        """
        Portas de saída que este nó produz.

        Returns:
            Lista de Port (pode ser vazia se nó é "sink").
        """
        pass

    @abstractmethod
    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Executa o nó com dados de entrada.

        Implementações devem:
        1. Validar inputs contra input_ports
        2. Executar a lógica (chamar API, processar, etc.)
        3. Retornar dict com chaves = output_ports.name
        4. Não lançar exceção não-tratada (usar logging + fallback)

        Args:
            inputs: Dict com chaves = input_ports.name
            context: ExecutionContext (tenant, tracer, metadados)

        Returns:
            Dict com chaves = output_ports.name

        Raises:
            ValueError: Se inputs inválidos
            RuntimeError: Se execução falhar (circuit breaker aberto, etc.)
        """
        pass

    @property
    def health_check(self) -> Optional[NodeHealthStatus]:
        """
        Check de saúde (circuit breaker, disponibilidade de recurso).

        Implementações opcionais devem retornar NodeHealthStatus.
        Se não implementado, assume-se nó saudável.

        Returns:
            NodeHealthStatus ou None (assume saudável).
        """
        return None

    @property
    def config_schema(self) -> Dict[str, Any]:
        """
        JSON Schema para validação de configuração dinâmica.

        Usado pra registrar em config_dinamica e validar valores.

        Exemplo:
            {
              "type": "object",
              "properties": {
                "model": {
                  "type": "string",
                  "default": "gemini-2.0-pro",
                  "description": "Modelo a usar"
                },
                "temperature": {
                  "type": "number",
                  "minimum": 0,
                  "maximum": 2,
                  "default": 1.0
                }
              },
              "required": ["model"]
            }

        Returns:
            JSON Schema (dict) ou {} se sem config dinâmica.
        """
        return {}

    @property
    def metadata(self) -> Dict[str, Any]:
        """
        Metadados do nó (nome, versão, descrição, autor, etc.).

        Usado pelo Hub pra mostrar info do nó.

        Returns:
            Dict com pelo menos: {
              "name": str,
              "type": str,
              "version": str,
              "description": str
            }
        """
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "To be overridden by subclass"
        }
