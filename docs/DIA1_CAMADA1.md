# ☀️ Dia 1 — Camada 1 (BaseNode + Registry)

> **Data**: 2026-09-02 (segunda-feira)  
> **Tempo**: Full day  
> **Saída esperada**: `base_node.py` + `node_registry.py` prontos + testes básicos verdes

---

## ⏰ Horário

- **09:00** — Setup + leitura desta página (15 min)
- **09:15** — Criar estrutura de diretórios (10 min)
- **09:30** — Escrever `base_node.py` (120 min)
- **11:30** — Lunch break (30 min)
- **12:00** — Escrever `node_registry.py` (90 min)
- **13:30** — Testes básicos `test_base_node.py` (60 min)
- **14:30** — Code review local / type checking (30 min)
- **15:00** — Commit + push (15 min)
- **15:15** — Reflection / notes (15 min)

---

## 🛠️ Setup (09:00–09:15)

### 1. Branch de trabalho

```bash
cd ~/Documents/py/Oraculo

# Puxar main (garantir atualizado)
git checkout main
git pull origin main

# Criar branch (nome seguindo convenção do projeto)
git checkout -b feature/camada1-basenodes-v1
```

### 2. Estrutura de diretórios

```bash
mkdir -p src/graph/nodes
touch src/graph/__init__.py
touch src/graph/nodes/__init__.py
```

### 3. Abrir editor

```bash
code src/graph/base_node.py
```

---

## 📝 Escrever `base_node.py` (09:30–11:30)

**Copie exatamente** da seção **3. `src/graph/base_node.py`** em [`SPRINT_CAMADA1_PLANEJAMENTO.md`](./SPRINT_CAMADA1_PLANEJAMENTO.md).

**Checklist enquanto copia**:
- [ ] Imports corretos (abc, dataclasses, enum, typing, uuid)
- [ ] `class PortType(str, Enum)` com valores (LLM_RESPONSE, EMBEDDINGS, etc.)
- [ ] `@dataclass class Port`
- [ ] `@dataclass class NodeHealthStatus`
- [ ] `@dataclass class ExecutionContext`
- [ ] `class BaseNode(ABC)` com todos os `@abstractmethod` e `@property`
- [ ] Docstrings em cada classe/method

**Depois**:

```bash
# Type checking
mypy src/graph/base_node.py

# Se errar: fix o erro, repita mypy até 0 erros
```

**Target**: 0 erros de tipo, sem warnings.

---

## 📝 Escrever `node_registry.py` (12:00–13:30)

**Copie exatamente** da seção **2. `src/graph/node_registry.py`** em [`SPRINT_CAMADA1_PLANEJAMENTO.md`](./SPRINT_CAMADA1_PLANEJAMENTO.md).

**Checklist**:
- [ ] `class NodeRegistry` com `__init__` e metodos
- [ ] `register()`, `register_factory()`, `get()`, `list_nodes()`, `validate_connection()`
- [ ] Função `get_registry()` singleton
- [ ] Função `_auto_discover()` (comentada ok se não completar)
- [ ] Docstrings

**Type checking**:
```bash
mypy src/graph/node_registry.py
```

---

## 🧪 Escrever Testes (13:30–14:30)

Crie `tests/unit/graph/test_base_node.py`:

```python
"""Testes de BaseNode e NodeRegistry."""

import pytest
from src.graph.base_node import BaseNode, Port, PortType, ExecutionContext


def test_base_node_is_abstract():
    """BaseNode não pode ser instanciado direto."""
    with pytest.raises(TypeError):
        BaseNode()


class MockNode(BaseNode):
    """Nó mock pra testes."""
    
    @property
    def node_id(self) -> str:
        return "mock_node"
    
    @property
    def node_type(self) -> str:
        return "mock"
    
    @property
    def input_ports(self):
        return [Port("input", PortType.TEXT, "Test input")]
    
    @property
    def output_ports(self):
        return [Port("output", PortType.TEXT, "Test output")]
    
    async def execute(self, inputs, context):
        return {"output": inputs.get("input", "").upper()}


def test_mock_node_instantiation():
    """Subclasses concretas podem ser instanciadas."""
    node = MockNode()
    assert node.node_id == "mock_node"
    assert node.node_type == "mock"


def test_port_creation():
    """Port pode ser criada com tipos corretos."""
    port = Port("test", PortType.LLM_RESPONSE, "Test port")
    assert port.name == "test"
    assert port.type_ == PortType.LLM_RESPONSE
    assert port.required is True


def test_execution_context():
    """ExecutionContext pode armazenar metadados."""
    ctx = ExecutionContext(tenant_id="UEMA")
    assert ctx.tenant_id == "UEMA"
    assert ctx.execution_id is not None  # UUID gerado


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**Rode os testes**:

```bash
pytest tests/unit/graph/test_base_node.py -v
```

**Target**: Todos os 5 testes PASS (✅).

---

## ✅ Code Review Local (14:30–15:00)

### Checklist de Qualidade

- [ ] Sem `TODO` comentários soltos
- [ ] Sem `print()` (use logging)
- [ ] Sem hardcoded values (magic numbers)
- [ ] Docstrings em classes e methods públicos
- [ ] Type hints completos (mypy 0 erros)
- [ ] Imports organizados (stdlib, third-party, local)

### Rodar tipo checking completo

```bash
mypy src/graph/ tests/unit/graph/
```

**Target**: 0 erros, 0 warnings.

---

## 🔧 Commit (15:00–15:15)

```bash
# Verificar status
git status

# Adicionar arquivos
git add src/graph/base_node.py
git add src/graph/node_registry.py
git add src/graph/__init__.py
git add src/graph/nodes/__init__.py
git add tests/unit/graph/test_base_node.py

# Verificar staging
git diff --cached

# Commit (siga convenção do projeto)
git commit -m "feat(graph): camada 1 — basenodes abstraction + registry

- BaseNode abstrato: interface comum pra providers
- Port/PortType: tipagem de entrada/saída
- NodeRegistry: registro centralizado com validação de tipos
- ExecutionContext: contexto de execução (tenant, tracer, etc)
- Testes básicos: instantiation, port creation, context

Specs em docs/SPRINT_CAMADA1_PLANEJAMENTO.md"

# Push pra criar PR
git push -u origin feature/camada1-basenodes-v1
```

---

## 📊 Reflection (15:15–15:30)

**Escreva curto**:

- [ ] Quanto tempo cada parte levou vs. planejado?
- [ ] Qual foi a parte mais fácil?
- [ ] Qual foi o bloqueador?
- [ ] O que aprender pra amanhã?

**Exemplo**:
```
REFLECTION — DIA 1

Timing:
- Setup: 10 min ✅
- base_node.py: 145 min (15 min extra type-fixing)
- node_registry.py: 85 min
- Testes: 50 min (mais rápido que esperado)
- Commit: 10 min

Fácil: Port + ExecutionContext (dataclasses pronto)
Bloqueador: typing imports (mypy exigiu Optional, Dict, etc. explícitos)
Amanhã: Passar mais tempo lendo spec antes de escrever

Saída: 2/2 arquivos prontos, 5/5 testes verdes ✅
```

---

## 🚨 Se Travar

| Problema | Solução |
|---|---|
| `ModuleNotFoundError` ao rodar testes | Adicione `__init__.py` nos diretórios |
| mypy reclama de imports | Use `from typing import Optional, Dict, ...` |
| Testes não rodam | `pip install pytest` ou confira `pytest.ini` |
| Type hints errados | Copie exatamente do `SPRINT_CAMADA1_PLANEJAMENTO.md` |
| Não consegue fazer commit | Confira `git status`, adicione arquivos com `git add` |

---

## ✨ Saída Esperada (End of Day)

```
✅ src/graph/base_node.py (250 linhas)
✅ src/graph/node_registry.py (150 linhas)
✅ tests/unit/graph/test_base_node.py (50 linhas)
✅ Testes verdes (5/5 PASS)
✅ mypy: 0 erros
✅ Branch criada, PR preparada
✅ Commit message descritiva
```

**Timeline**: PR abre no final do dia 1, code review começa Dia 2.

---

## Dia 2–5: Próximas Tarefas

(Não se preocupe com isso hoje, mas para referência)

- **Dia 2**: Refactor `llm_provider_registry.py` → herdar de `LLMNode`
- **Dia 3–4**: Testes de LLMNode + integration
- **Dia 5**: Code review + merge pra `main`

---

## 📞 Contato

Dúvida rápida? Abra issue em `docs/` com tag `camada1-question`.

---

**Boa sorte no Dia 1! 🚀 See you at EOD standup.**
