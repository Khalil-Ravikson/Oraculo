# 🧪 Como Rodar Testes — Camada 1

> **Quick start**: Comandos pra rodar testes localmente.

---

## ✅ Arquivos Criados

```
src/graph/
├── __init__.py                    ← Exports principais
├── base_node.py                   ← Abstração BaseNode (250 linhas)
├── execution_context.py           ← ExecutionContext (dataclass)
├── node_registry.py               ← NodeRegistry (150 linhas)
└── nodes/
    └── __init__.py                ← (vazio, para Fase 6)

tests/unit/graph/
├── __init__.py
├── test_base_node.py              ← 16 testes
└── test_node_registry.py          ← 18 testes
```

**Total**: ~1.500 linhas de código, ~700 linhas de testes.

---

## 🚀 Rodar Testes (3 formas)

### 1. **Tudo (recomendado)**

```bash
# Roda todos os testes de Camada 1
pytest tests/unit/graph/ -v
```

**Saída esperada**:
```
tests/unit/graph/test_base_node.py::TestPortType::test_port_type_values PASSED
tests/unit/graph/test_base_node.py::TestPortType::test_port_type_is_string_enum PASSED
tests/unit/graph/test_base_node.py::TestPort::test_port_creation PASSED
...
tests/unit/graph/test_node_registry.py::TestGlobalRegistry::test_reset_registry PASSED

====== 34 passed in 0.45s ======
```

### 2. **Só BaseNode**

```bash
pytest tests/unit/graph/test_base_node.py -v
```

**Conta**: 16 testes

### 3. **Só NodeRegistry**

```bash
pytest tests/unit/graph/test_node_registry.py -v
```

**Conta**: 18 testes

### 4. **Um teste específico**

```bash
pytest tests/unit/graph/test_base_node.py::TestPort::test_port_creation -v
```

---

## 🔍 Type Checking (mypy)

```bash
# Verificar tipos
mypy src/graph/ --strict
```

**Saída esperada**: `Success: no issues found`

**Se errar**:
```bash
# Mais detalhado
mypy src/graph/ --show-error-codes --show-error-context
```

---

## 📊 Coverage (cobertura de testes)

```bash
# Rodar com cobertura
pytest tests/unit/graph/ --cov=src.graph --cov-report=html

# Abrir relatório
open htmlcov/index.html  # Mac
start htmlcov/index.html  # Windows
```

**Target**: >= 90% de cobertura

---

## 🚨 Troubleshooting

### `ModuleNotFoundError: No module named 'src'`

**Solução**: Adicione `src/` ao PYTHONPATH:

```bash
# Mac/Linux
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest tests/unit/graph/ -v

# Windows (PowerShell)
$env:PYTHONPATH = "$(Get-Location);$env:PYTHONPATH"
pytest tests/unit/graph/ -v

# Windows (CMD)
set PYTHONPATH=%CD%;%PYTHONPATH%
pytest tests/unit/graph/ -v
```

### `ImportError: cannot import name 'BaseNode'`

**Solução**: Confira que arquivos estão no lugar certo:
```bash
ls -la src/graph/base_node.py
ls -la src/graph/__init__.py
```

### Testes não encontram `pytest`

**Solução**: Instale pytest:
```bash
pip install pytest pytest-asyncio pytest-cov
```

---

## ✅ Pre-commit Check (antes de fazer commit)

```bash
# Rodar tudo que dev deve rodar antes de commit
./scripts/precommit-check.sh
```

Ou manualmente:

```bash
# 1. Type checking
mypy src/graph/ --strict

# 2. Linting (opcional, se tiver)
flake8 src/graph/ tests/unit/graph/

# 3. Testes
pytest tests/unit/graph/ -v

# 4. Se tudo verde, commit
git add src/graph/ tests/unit/graph/
git commit -m "feat(graph): camada 1 - basenodes abstraction"
```

---

## 🎯 CI/CD (GitHub Actions)

Quando der push, CI vai rodar automaticamente (se configurado):

```yaml
# .github/workflows/tests.yml
- name: Run graph tests
  run: pytest tests/unit/graph/ -v --cov=src.graph
```

---

## 📈 Métricas Esperadas (Dia 1)

| Métrica | Esperado |
|---|---|
| Testes passando | 34/34 ✅ |
| Type checking | 0 erros |
| Cobertura | >= 85% |
| Tempo de execução | < 1s |

---

## 🔗 Próximos Testes (Dia 2+)

Quando refatorar LLMNode:

```bash
pytest tests/unit/graph/test_llm_node.py -v
```

Quando criar topologia:

```bash
pytest tests/unit/graph/test_graph_topology.py -v
```

---

## 💡 Dicas

1. **Rodar um teste enquanto desenvolve**:
   ```bash
   pytest tests/unit/graph/test_base_node.py::TestPort -v -s
   ```

2. **Rodar testes em watch mode** (rerun ao salvar):
   ```bash
   pip install pytest-watch
   ptw tests/unit/graph/ -- -v
   ```

3. **Ver output de print() nos testes**:
   ```bash
   pytest tests/unit/graph/ -v -s
   ```

4. **Rodar só testes que falharam** (útil após fix):
   ```bash
   pytest tests/unit/graph/ --lf -v
   ```

---

## 🎓 Entender os Testes

Abra `test_base_node.py`:
- `TestPortType` — testa enum PortType
- `TestPort` — testa criação e validação de Port
- `TestNodeHealthStatus` — testa status de saúde
- `TestExecutionContext` — testa contexto de execução
- `TestBaseNode` — testa abstração BaseNode e subclasses

Cada test class é independente. Rodar um:

```bash
pytest tests/unit/graph/test_base_node.py::TestPort -v
```

---

## 📞 Help

**Teste não passa?**
1. Copie o output de erro
2. Abra `SPRINT_CAMADA1_PLANEJAMENTO.md`
3. Confira spec

**Erro de import?**
1. Confira `__init__.py` files existem
2. Confira PYTHONPATH está certo
3. Rode `pip install -e .` (editable install)

---

**Boa sorte! 🚀 Qualquer dúvida, abra issue em `docs/`.**
