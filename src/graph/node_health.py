"""
src/graph/node_health.py — saúde barata dos nós do registry (Hub v2, Sprint 4)
=============================================================================
`BaseNode.health_check` existe mas todos os wrappers herdam o default `None`.
Em vez de espalhar um `health_check` por arquivo (9 wrappers), este módulo
resolve a saúde de forma centralizada, só com probes que custam ~0
(nenhuma chamada de rede na renderização da página):

  - `llm_provider`: provedor global ativo tem credencial? circuito fechado?
  - resto: `None` = "não medido" (a UI mostra "Desconhecido", não "Erro").

Latência real de servidor MCP fica no botão "Testar Conexão" da página
`/hub/mcp-servers`, não aqui.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolver(node_type: str) -> dict | None:
    """Retorna `{"is_healthy": bool, "error"|"detail": str}` ou `None`
    (não medido). Nunca lança."""
    try:
        if node_type == "llm_provider":
            return _saude_llm()
    except Exception as exc:  # noqa: BLE001
        logger.debug("node_health(%s) falhou: %s", node_type, exc)
    return None


def _saude_llm() -> dict:
    from src.infrastructure.adapters import llm_circuit_breaker, llm_provider_registry
    from src.infrastructure.adapters.llm_factory import _provider_global_ativo

    ativo = _provider_global_ativo()
    tem_credencial = llm_provider_registry.health_check(ativo)
    if tem_credencial is False:
        return {"is_healthy": False, "error": f"provedor ativo '{ativo}' sem credencial"}
    if not llm_circuit_breaker.permitir(ativo):
        return {"is_healthy": False, "error": f"provedor ativo '{ativo}' bloqueado por falhas"}
    return {"is_healthy": True, "detail": f"provedor ativo: {ativo}"}
