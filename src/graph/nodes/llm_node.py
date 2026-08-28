"""LLMNode — wrapper de BaseNode sobre llm_factory.get_llm_provider()."""

from typing import Any, Dict, List, Optional, Type
from src.graph.base_node import BaseNode, Port, PortType
from src.graph.execution_context import ExecutionContext


class LLMNode(BaseNode):
    """
    Nó de chamada a modelo de linguagem.

    Delega para `llm_factory.get_llm_provider()`, que já resolve o
    provider ativo (override por agente → Redis global → settings),
    aplica circuit breaker (`llm_circuit_breaker.py`) e instrumenta
    telemetria (`MonitoredLLMProvider`). Este nó não reimplementa nada —
    só expõe o factory existente sob a interface BaseNode.

    Dois modos de execução:
    - Sem `response_schema`: texto livre via `gerar_resposta_async()`,
      retorna `response` (texto) + `tokens_used`.
    - Com `response_schema` (classe Pydantic passada em `inputs`): usa
      `gerar_resposta_estruturada_async()`, retorna `structured` (instância
      do schema) em vez de `response`.

    `agente`/`rota` (opcionais em `inputs`) são repassados ao factory pra
    resolver override de provider por agente e telemetria por rota —
    mesmo contrato que `get_llm_provider(agente, rota)` já usa hoje.
    """

    @property
    def node_id(self) -> str:
        return "llm_default"

    @property
    def node_type(self) -> str:
        return "llm_provider"

    @property
    def input_ports(self) -> List[Port]:
        return [
            Port(
                name="prompt",
                type_=PortType.TEXT,
                description="Texto do prompt a enviar ao modelo"
            ),
            Port(
                name="system_instruction",
                type_=PortType.TEXT,
                description="Instrução de sistema (opcional)",
                required=False
            ),
            Port(
                name="temperatura",
                type_=PortType.NUMBER,
                description="Temperatura de geração (default 0.2, ou 0.0 se response_schema)",
                required=False
            ),
            Port(
                name="response_schema",
                type_=PortType.CUSTOM,
                description="Classe Pydantic opcional — ativa modo de resposta estruturada",
                required=False
            ),
            Port(
                name="agente",
                type_=PortType.TEXT,
                description="Nome do agente (resolve override de provider por agente)",
                required=False
            ),
            Port(
                name="rota",
                type_=PortType.TEXT,
                description="Rota (telemetria)",
                required=False
            ),
        ]

    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="response",
                type_=PortType.LLM_RESPONSE,
                description="Resposta em texto do modelo (modo livre)"
            ),
            Port(
                name="structured",
                type_=PortType.STRUCTURED,
                description="Instância do response_schema (modo estruturado)"
            ),
            Port(
                name="tokens_used",
                type_=PortType.TOKENS,
                description="Tupla (input_tokens, output_tokens)"
            ),
        ]

    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        prompt = inputs.get("prompt")
        if not prompt:
            raise ValueError("'prompt' is required")

        from src.infrastructure.adapters.llm_factory import get_llm_provider

        agente = inputs.get("agente")
        rota = inputs.get("rota", "")
        provider = get_llm_provider(agente, rota)

        response_schema: Optional[Type] = inputs.get("response_schema")

        if response_schema is not None:
            structured = await provider.gerar_resposta_estruturada_async(
                prompt,
                response_schema,
                system_instruction=inputs.get("system_instruction", ""),
                temperatura=inputs.get("temperatura", 0.0),
                rota=rota,
            )
            return {
                "structured": structured,
                "tokens_used": provider.ultimo_uso_tokens,
            }

        result = await provider.gerar_resposta_async(
            prompt,
            system_instruction=inputs.get("system_instruction", ""),
            temperatura=inputs.get("temperatura", 0.2),
            rota=rota,
        )

        if not result.sucesso:
            raise RuntimeError(f"LLM call failed: {result.erro}")

        return {
            "response": result.conteudo,
            "tokens_used": (result.input_tokens, result.output_tokens),
        }

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "LLM via llm_factory (provider resolvido por agente/Redis/settings, circuit breaker embutido)",
        }
