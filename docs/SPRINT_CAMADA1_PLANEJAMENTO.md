# Sprint: Camada 1 (BaseNode + NodeRegistry) — Plano Detalhado

> **Status**: ✅ APROVADO — Começar segunda-feira (2026-09-02 ou próximo dia útil)  
> **Duração estimada**: 1–2 sprints  
> **Objetivo**: Unificar todos os providers (LLM, STT, TTS, Embeddings, Parsers, Tools) sob abstração comum.

---

## 🎯 Visão em Uma Linha

**Todos os providers herdam de `BaseNode`; Hub mostra registry unificado com validação de tipos.**

---

## 📋 Histórico de Decisão

| Data | Evento |
|---|---|
| 2026-08-28 | Roadmap proposto (Fases 6–11) |
| 2026-08-28 | Decisão: **SIM — Camada 1 agora** ✅ |
| 2026-08-29 | Sprint planning iniciado |
| 2026-09-02 | Sprint 1 — Camada 1 base + LLM refactor |

---

## 🏗️ Arquitetura — O que será criado

### Novos Arquivos

```
src/graph/
├── base_node.py              # Abstração BaseNode (ABC)
├── node_registry.py          # NodeRegistry (autodiscovery)
├── execution_context.py      # ExecutionContext (metadados de execução)
├── nodes/
│   ├── __init__.py
│   ├── llm_node.py           # LLMNode (refactor de llm_factory)
│   ├── parser_node.py        # ParserNode (refactor de parser_factory)
│   ├── tool_node.py          # ToolNode (refactor de capabilities/registry)
│   ├── stt_node.py           # STTNode (novo, Fase 6)
│   ├── tts_node.py           # TTSNode (novo, Fase 6)
│   ├── embeddings_node.py    # EmbeddingsNode (novo, Fase 6)
│   └── channel_node.py       # ChannelNode (novo, Fase 7, stub)
│
└── tests/
    ├── test_base_node.py
    ├── test_node_registry.py
    ├── test_llm_node.py
    ├── test_parser_node.py
    └── test_tool_node.py
```

### Arquivos Modificados

```
src/infrastructure/
├── adapters/
│   └── llm_provider_registry.py  # Herdar de BaseNode
├── dynamic_config.py             # Sem mudança (segue sendo lido)
└── route_registry.py             # Sem mudança (rota → grafo)

src/capabilities/
└── registry.py                   # Refactor pra BaseNode

templates/hub/
├── graph-nodes.html              # Página nova: registry visual
└── (outros: sem mudança)

migrations/
└── versions/
    └── 013_graph_topology.py     # Tabelas: graph_topology, graph_node_bindings
```

---

## 📝 Especificação de Código

### 1. `src/graph/base_node.py` — Abstração (nova)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Dict, List
import uuid

class PortType(str, Enum):
    """Tipos de porta padrão."""
    LLM_RESPONSE = "llm_response"
    EMBEDDINGS = "embeddings"
    TOKENS = "tokens"
    TEXT = "text"
    STRUCTURED = "structured"
    AUDIO = "audio"
    FILE = "file"
    # ... mais conforme necessário

@dataclass
class Port:
    """Definição de porta de entrada/saída."""
    name: str
    type_: PortType | str
    description: str
    required: bool = True
    schema: Optional[Dict[str, Any]] = None

@dataclass
class NodeHealthStatus:
    """Status de saúde de um nó."""
    is_healthy: bool
    last_checked: str  # ISO8601
    error_message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

@dataclass
class ExecutionContext:
    """Contexto durante execução de um nó."""
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    parent_execution_id: Optional[str] = None
    tracer: Optional[Any] = None  # OpenTelemetry tracer
    metadata: Dict[str, Any] = field(default_factory=dict)

class BaseNode(ABC):
    """Abstração comum para todos os provedores e agentes."""
    
    @property
    @abstractmethod
    def node_id(self) -> str:
        """
        Identificador único do nó.
        Ex: 'llm_primary', 'rag_search', 'stt_whisper', 'tool_email'
        """
        pass
    
    @property
    @abstractmethod
    def node_type(self) -> str:
        """
        Tipo de nó (para UI/registry).
        Ex: 'llm_provider', 'stt_provider', 'tool', 'agent'
        """
        pass
    
    @property
    @abstractmethod
    def input_ports(self) -> List[Port]:
        """Portas de entrada que este nó espera."""
        pass
    
    @property
    @abstractmethod
    def output_ports(self) -> List[Port]:
        """Portas de saída que este nó produz."""
        pass
    
    @abstractmethod
    async def execute(
        self, 
        inputs: Dict[str, Any], 
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """
        Executa o nó com dados de entrada.
        
        Returns:
            Dict com chaves = output_ports names
        """
        pass
    
    @property
    def health_check(self) -> Optional[NodeHealthStatus]:
        """
        Check de saúde (circuit breaker, etc.).
        None = não implementado (assume saudável).
        """
        return None
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        """
        JSON Schema pra validação de configuração dinâmica.
        Exemplo:
        {
          "type": "object",
          "properties": {
            "model": {"type": "string", "default": "gemini-2.0-pro"},
            "temperature": {"type": "number", "min": 0, "max": 2}
          }
        }
        """
        return {}
    
    @property
    def metadata(self) -> Dict[str, Any]:
        """
        Metadados do nó (versão, autor, descrição, etc.).
        """
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": "To be overridden"
        }
```

### 2. `src/graph/node_registry.py` — Registry (nova)

```python
from typing import Dict, Callable, Optional, List, Type
from src.graph.base_node import BaseNode, Port
import pkgutil
import importlib

class NodeRegistry:
    """Registry central de nós (autodiscovery + registration)."""
    
    def __init__(self):
        self._nodes: Dict[str, BaseNode] = {}
        self._factories: Dict[str, Callable[..., BaseNode]] = {}
    
    def register(self, node_id: str, node: BaseNode) -> None:
        """Registra uma instância de nó."""
        self._nodes[node_id] = node
    
    def register_factory(
        self, 
        node_type: str, 
        factory: Callable[..., BaseNode]
    ) -> None:
        """Registra uma factory pra criar nós de um tipo."""
        self._factories[node_type] = factory
    
    def get(self, node_id: str) -> Optional[BaseNode]:
        """Busca um nó registrado."""
        return self._nodes.get(node_id)
    
    def list_nodes(self) -> List[Dict]:
        """Lista todos os nós registrados com metadados."""
        return [
            {
                "id": node.node_id,
                "type": node.node_type,
                "metadata": node.metadata,
                "health": node.health_check,
                "input_ports": [
                    {"name": p.name, "type": p.type_, "required": p.required}
                    for p in node.input_ports
                ],
                "output_ports": [
                    {"name": p.name, "type": p.type_}
                    for p in node.output_ports
                ],
            }
            for node in self._nodes.values()
        ]
    
    def validate_connection(
        self, 
        source_node_id: str, 
        output_port: str,
        target_node_id: str,
        input_port: str
    ) -> tuple[bool, Optional[str]]:
        """
        Valida se uma conexão é permitida (tipos casam).
        Returns: (is_valid, error_message)
        """
        source = self.get(source_node_id)
        target = self.get(target_node_id)
        
        if not source or not target:
            return False, "Node not found"
        
        source_output = next(
            (p for p in source.output_ports if p.name == output_port),
            None
        )
        target_input = next(
            (p for p in target.input_ports if p.name == input_port),
            None
        )
        
        if not source_output or not target_input:
            return False, "Port not found"
        
        if source_output.type_ != target_input.type_:
            return False, f"Type mismatch: {source_output.type_} != {target_input.type_}"
        
        return True, None

# Singleton global
_global_registry: Optional[NodeRegistry] = None

def get_registry() -> NodeRegistry:
    """Retorna o registry global (lazy init)."""
    global _global_registry
    if _global_registry is None:
        _global_registry = NodeRegistry()
        _auto_discover()
    return _global_registry

def _auto_discover() -> None:
    """Autodiscovery: procura por classes BaseNode em src/graph/nodes/"""
    registry = _global_registry
    nodes_package = "src.graph.nodes"
    
    # Import dinâmico de todos os módulos em nodes/
    nodes_path = __import__("src.graph.nodes").__path__
    for importer, modname, ispkg in pkgutil.iter_modules(nodes_path):
        module = importlib.import_module(f"{nodes_package}.{modname}")
        
        # Procura por classes que herdam de BaseNode
        for name in dir(module):
            obj = getattr(module, name)
            if (isinstance(obj, type) and 
                issubclass(obj, BaseNode) and 
                obj is not BaseNode):
                # Instancia e registra
                try:
                    instance = obj()
                    registry.register(instance.node_id, instance)
                except Exception as e:
                    print(f"Warning: couldn't instantiate {name}: {e}")
```

### 3. `src/graph/nodes/llm_node.py` — Refactor do LLM Provider (novo)

```python
from src.graph.base_node import BaseNode, Port, PortType, ExecutionContext
from src.infrastructure.adapters.llm_provider_registry import (
    get_llm_provider,
    get_circuit_breaker
)
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class LLMNode(BaseNode):
    """Nó que representa um provedor de LLM."""
    
    def __init__(self, provider_name: str = "gemini", model: str = "gemini-2.0-pro"):
        self.provider_name = provider_name
        self.model = model
    
    @property
    def node_id(self) -> str:
        return f"llm_{self.provider_name}"
    
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
                name="context",
                type_=PortType.STRUCTURED,
                description="Contexto adicional (opcional)",
                required=False
            )
        ]
    
    @property
    def output_ports(self) -> List[Port]:
        return [
            Port(
                name="response",
                type_=PortType.LLM_RESPONSE,
                description="Resposta do modelo LLM"
            ),
            Port(
                name="tokens_used",
                type_=PortType.TOKENS,
                description="Contagem de tokens usados"
            )
        ]
    
    async def execute(
        self,
        inputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """Chama o provider LLM com circuit breaker."""
        prompt = inputs.get("prompt")
        if not prompt:
            raise ValueError("'prompt' is required")
        
        # Busca provider (usa circuit breaker)
        provider = get_llm_provider(self.provider_name)
        circuit_breaker = get_circuit_breaker(self.provider_name)
        
        try:
            # Executa com proteção de circuit breaker
            response, tokens_used = await circuit_breaker.execute(
                lambda: provider.call(prompt, model=self.model)
            )
            
            return {
                "response": response,
                "tokens_used": tokens_used
            }
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise
    
    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "name": self.node_id,
            "type": self.node_type,
            "version": "1.0.0",
            "description": f"LLM Provider: {self.provider_name} ({self.model})",
            "provider": self.provider_name,
            "model": self.model
        }
```

---

## 🧪 Testes — O que validar

### Unit Tests (criar em `tests/unit/graph/`)

1. **`test_base_node.py`**
   - Verificar que `BaseNode` é ABC (não pode instanciar direto)
   - Verificar que subclasses devem implementar `node_id`, `execute`, etc.

2. **`test_node_registry.py`**
   - Registrar nó → `get()` retorna
   - `list_nodes()` mostra todos
   - Validar conexão: tipos casam ✅, tipos diferentes ❌
   - Autodiscovery encontra `LLMNode`, `ParserNode`, etc.

3. **`test_llm_node.py`**
   - Execute com input válido → output esperado
   - Circuit breaker abre sob falha simulada
   - Metadados retornam schema correto

4. **`test_parser_node.py`** (similar)

5. **`test_tool_node.py`** (similar)

### Integration Tests

1. **`test_graph_topology_validation.py`**
   - Criar topologia JSON com 2 nós, 1 aresta
   - Validar tipos de porta
   - Executar grafo end-to-end

2. **`test_degradation_scenarios.py`**
   - Postgres fora → lê config default
   - Redis fora → lê Postgres
   - Provider LLM fora → circuit breaker abre

---

## 📊 Checklist de Implementação

### Semana 1 (Sprint 1)

- [ ] **Dia 1-2**: Criar `base_node.py` + `node_registry.py` (150 linhas)
- [ ] **Dia 2-3**: Testes básicos de `BaseNode` + registry (100 linhas de testes)
- [ ] **Dia 3**: Refactor `llm_provider_registry.py` → herdar de `BaseNode`
- [ ] **Dia 4-5**: Testes de `LLMNode` → validar comportamento idêntico ao antes
- [ ] **Dia 5**: Code review + merge pra branch

**Saída esperada**: LLM provider refatorado, 100% verde em testes, comportamento idêntico.

### Semana 2 (Sprint 1 cont. ou Sprint 2)

- [ ] **Dia 1-2**: Refactor `parser_factory.py` → `ParserNode`
- [ ] **Dia 2-3**: Refactor `capabilities/registry.py` → `ToolNode`
- [ ] **Dia 3-4**: Testes de topologia (2+ nós em grafo)
- [ ] **Dia 5**: Integração com Hub (`/hub/graph-nodes`)

**Saída esperada**: Todos os providers principais (LLM, Parser, Tool) herdando de `BaseNode`.

### Validação Final

- [ ] Todos os testes verdes (`pytest tests/unit/graph/`)
- [ ] Sem regressão em funcionalidade existente
- [ ] Hub mostra `/hub/graph-nodes` com registry visual
- [ ] Code review aprovado
- [ ] Documento de "Migration guide" pra dev que tocar providers

---

## 🚀 Como Começar (Dia 1 — Segunda-feira)

### Setup

```bash
# Puxar branch principal
git checkout main
git pull origin main

# Criar branch de trabalho
git checkout -b feature/camada1-basenodes

# Abrir editor
code src/graph/base_node.py
```

### Passos

1. **Copie a especificação acima** → `src/graph/base_node.py` (sem testes ainda)
2. **Rode tipo checking**: `mypy src/graph/base_node.py` → 0 erros
3. **Escreva testes mínimos**: `test_base_node.py` → verificar ABC
4. **Commit**: `git commit -m "feat(graph): base node abstraction"`
5. **Prepare node_registry.py** → similar

**Checklist do Dia 1**: `base_node.py` pronto + testes básicos verdes.

---

## 🎓 Documentação Interna

Adicione docstring em cada classe:

```python
class BaseNode(ABC):
    """
    Abstração comum para todos os nós do grafo.
    
    Um nó é um componente executável que:
    - Recebe dados de entrada via portas tipadas
    - Executa processamento
    - Produz dados de saída
    
    Exemplos:
    - LLMNode: chama modelo de IA
    - ParserNode: parseia documento
    - ToolNode: executa ferramenta
    - STTNode (futuro): transcreve áudio
    
    Uso:
        node = LLMNode(provider_name="gemini")
        result = await node.execute(
            {"prompt": "Olá"},
            ExecutionContext(tenant_id="UEMA")
        )
    """
```

---

## 📝 Definição de Pronto (Definition of Done)

Camada 1 está pronta quando:

- ✅ `base_node.py` + `node_registry.py` implementados
- ✅ LLM, Parser, Tool providers herdam de `BaseNode`
- ✅ Testes unitários: 100% verde
- ✅ Testes de integração: grafo simples (2+ nós) executa
- ✅ Zero regressão (comportamento idêntico ao antes)
- ✅ Hub `/hub/graph-nodes` lista todos os nós
- ✅ Code review aprovado
- ✅ Documentation atualizada (`.claude.md` menciona Camada 1)
- ✅ Merge pra `main`

---

## 📞 Pontos de Contato / Help

**Dúvida**: "Como herdar de BaseNode em um provider existente?"  
→ Ver `src/graph/nodes/llm_node.py` acima (exemplo pronto)

**Dúvida**: "Quais são os PortTypes válidos?"  
→ Ver `enum PortType` em `base_node.py`

**Dúvida**: "Circuit breaker já existe?"  
→ Sim, `src/infrastructure/adapters/llm_circuit_breaker.py` (Fase 3 já fez)

**Dúvida**: "Registry precisa ser thread-safe?"  
→ Sim, adicione `threading.Lock` em `NodeRegistry` se rodar em paralelo.

---

## 🎯 Próximo Passo Após Camada 1

Assim que Camada 1 estiver ✅ concluída e em `main`:
- Iniciar **Fase 6** (STT/TTS/Embeddings nodes)
- Seguir padrão já comprovado em LLMNode
- Mesmo checklist de testes

---

## Resumo

| Aspecto | Detalhe |
|---|---|
| **Objetivo** | Unificar providers sob abstração comum |
| **Escopo** | `base_node.py` + `node_registry.py` + refactor LLM/Parser/Tool |
| **Tempo** | 1–2 sprints |
| **Risco** | Baixo-médio (refator com testes) |
| **Bloqueador** | Nenhum — pode rodar paralelo a outras Fases |
| **Início** | 2026-09-02 (segunda-feira próxima) |
| **Sign-off** | Code review + merge pra `main` |

---

**Boa sorte! 🚀 Qualquer dúvida, abra issue em `docs/` ou marque reunião.**
