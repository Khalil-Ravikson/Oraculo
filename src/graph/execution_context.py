"""Contexto de execução para nós do grafo."""

from dataclasses import dataclass, field
from typing import Any, Optional, Dict
import uuid
from datetime import datetime, timezone


@dataclass
class ExecutionContext:
    """
    Contexto de execução durante processamento de um nó.

    Armazena metadados sobre a execução atual, como tenant, tracing,
    parent calls para grafo aninhado, etc.
    """

    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Identificador único desta execução."""

    tenant_id: Optional[str] = None
    """ID do tenant (isolamento multi-tenant). None = global/UEMA."""

    parent_execution_id: Optional[str] = None
    """Se este nó é parte de um grafo aninhado, ID do parent."""

    tracer: Optional[Any] = None
    """OpenTelemetry tracer (opcional, pra spans/traces)."""

    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """ISO8601 timestamp do início da execução."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Metadados arbitrários (user_id, session_id, etc.)."""

    def __post_init__(self):
        """Validação pós-inicialização."""
        if self.execution_id is None:
            self.execution_id = str(uuid.uuid4())

    def with_child(self, child_tenant_id: Optional[str] = None) -> "ExecutionContext":
        """
        Cria um ExecutionContext filho (pra grafo aninhado).

        Args:
            child_tenant_id: Tenant do child (default: mesmo do parent).
        """
        return ExecutionContext(
            parent_execution_id=self.execution_id,
            tenant_id=child_tenant_id or self.tenant_id,
            tracer=self.tracer,
            metadata=self.metadata.copy()
        )

    def set_metadata(self, key: str, value: Any) -> None:
        """Define uma chave de metadado."""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Busca uma chave de metadado."""
        return self.metadata.get(key, default)
