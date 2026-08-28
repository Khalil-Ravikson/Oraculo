# Decisão: Iniciar Camada 1 (BaseNode + NodeRegistry) — Fase 6 Agora

> **Propósito**: Desbloquear Fases 6–8 e unificar todos os providers sob abstração comum.  
> **Decisão pendente do dono**: SIM ou NÃO?

---

## Contexto

As Fases 1–5 do plano de plataforma orientada a configuração estão **✅ concluídas e em produção**. Cada componente (LLM, Parser, Tool) tem seu próprio padrão:

- `llm_factory.py` → interface `Protocol` + dict de builders + circuit breaker
- `parser_factory.py` → dict de builders + lista de candidatos + probe
- `tool_registry.py` → decorator + autodiscovery

**Padrão comum**: registry de providers, cada um com forma ligeiramente diferente.

**O adendo de nós declarativos** (`arquitetura_nos_declarativa.md`) propõe **Camada 1**: abstração `BaseNode` que todos herdassem.

**Bloqueio**: Sem Camada 1, as Fases 6–8 (STT/TTS, Channels, MCP) não têm alicerce. Com ela, tudo fica consistente.

---

## O que é Camada 1 (estimado: 1–2 sprints)

```python
# src/graph/base_node.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class Port:
    name: str
    type_: str  # "llm_response", "embeddings", "tokens", etc.
    description: str

class BaseNode(ABC):
    """Abstração comum para todos os provedores e agentes."""
    
    @property
    @abstractmethod
    def node_id(self) -> str:
        """Ex: 'llm_primary', 'rag_search', 'stt_whisper'"""
        pass
    
    @property
    @abstractmethod
    def input_ports(self) -> list[Port]:
        """Portas de entrada esperadas"""
        pass
    
    @property
    @abstractmethod
    def output_ports(self) -> list[Port]:
        """Portas de saída que este nó produz"""
        pass
    
    @abstractmethod
    async def execute(
        self, 
        inputs: dict[str, Any], 
        context: ExecutionContext
    ) -> dict[str, Any]:
        """Executa o nó com dados de entrada"""
        pass
    
    @property
    def health_check(self) -> Optional[HealthCheck]:
        """Circuit breaker / health monitor (opcional)"""
        return None

    @property
    def config_schema(self) -> dict:
        """JSON Schema pra validação de configuração dinâmica"""
        return {}
```

**Resultado**: Todos os providers — LLM, STT, TTS, Embeddings, Parsers, Tools, agentes — herdam de `BaseNode`. Hub mostra registry unificado. Grafo valida conexões por tipo de porta.

---

## Benefícios imediatos

| Benefício | Vale? | Prioridade |
|---|---|---|
| **Unificação visual** — Hub mostra "todos os provedores" num único lugar | ✅ SIM | Alta |
| **Validação de grafo** — conectar output de nó X a input de nó Y só funciona se tipos casam | ✅ SIM | Alta |
| **Circuit breaker uniforme** — todo provider tem health check, não só LLM | ✅ SIM | Média |
| **Manifesto de capability** — cada nó declara versão de interface, config esperada | ✅ SIM | Média |
| **Grafo em produção** — preparar terrain pra Fase 9 (multi-tenant grafos) | ✅ SIM | Média |
| **Simplifica Fase 7–8** — Channel e MCP são só mais um `BaseNode`, não coisa especial | ✅ SIM | Alta (reduz risco) |

---

## Custo de **não** fazer agora

Se pular Camada 1:
- Fases 6–8 replicam padrão de providers manualmente (3 vezes).
- Refatoração futura será 2x mais cara (já tem código em produção).
- Hub fica fragmentado: "LLM Manager" + "Channel Manager" + "MCP Manager" separados.

---

## Risco de fazer agora

- **Refatoração de código vivo**: `llm_factory.py`, `parser_factory.py` já estão em produção rodando Fases 1–5. Herdar de `BaseNode` é mudança de assinatura.
- **Mitigação**: Refatorar com testes verdes, garantir comportamento idêntico (regression test).
- **Tempo**: 1–2 sprints, não é bloqueador de negócio (só limpa arquitetura).

---

## Recomendação

**🟢 INICIAR Camada 1 agora.** Motivos:

1. Base sólida pra Fases 6–8.
2. Reduz risco de Fase 7 (Channel) e Fase 8 (MCP) — são "só mais um nó".
3. Alinha com visão LangGraph Studio (nós + arestas).
4. Não é bloqueador — pode rodar paralelo a Fase 6 (STT/TTS refactoring).
5. Tempo curto (2 sprints).

**Próximo passo**: Refatorar `llm_factory.py` pra herdar de `BaseNode`, validar com testes, depois STT/TTS/Embeddings seguem o padrão já comprovado.

---

## Se decidir NÃO fazer agora

- Fases 6–8 precisam de um documento separado "padrões sem Camada 1".
- Risco aumentado em Fase 7–8 (cada um inventa seu padrão).
- Camada 1 fica pra "depois de Fase 6", o que provavelmente nunca acontece.

**Recomendação**: Não recomendado, mas é opcional.

---

## Próxima conversa (se SIM)

1. Cronograma: Camada 1 + refactor LLM = Sprint X.
2. Qual é o "breaking change" aceitável? (Versão de API, release note?)
3. Testes: cobertura esperada?
4. Quem revisa (code review)?
