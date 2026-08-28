# Oráculo — Arquitetura de Nós Declarativa (grafo LangGraph montado a partir de dados)

> Investigação arquitetural. Complementa `plataforma_orientada_a_configuracao.md`.
> Escopo: transformar a construção do `StateGraph` do LangGraph — hoje 100%
> hardcoded em `langgraph_experiment/graph.py` — em algo montado a partir de uma
> definição declarativa (JSON/YAML), com nós descobertos automaticamente e
> descritos por metadados, no estilo n8n / Langflow / LangGraph Studio.
>
> **Status: proposta, não implementada.** Nada aqui foi codificado.

---

## Contexto — de onde veio o pedido

O pedido original (resumido): "quero um sistema de nós descobertos
automaticamente, registrados com metadados (nome, descrição, inputs, outputs,
categoria, ícone), e poder montar o grafo LangGraph a partir de um JSON com
`nodes[]` e `edges[]`". Referências citadas: o artigo de plugin hot-reload para
LangGraph no LinkedIn, `langgraph.json` do LangGraph Studio, componentes do
Langflow, custom nodes do n8n, e o adapter `wb_json_to_langgraph` do
workflowbuilder.io.

Este documento avalia **se** e **quanto** disso vale para o Oráculo
especificamente, e propõe um desenho.

---

## A. O que o Oráculo já tem desse padrão (e o que falta)

O plano-mãe (`plataforma_orientada_a_configuracao.md`) já moveu boa parte do
sistema para "comportamento é dado, não código". Três registries já existem e
são exatamente instâncias do padrão pedido:

| Registry | O que descreve | Onde vive o dado | Editável em runtime |
|---|---|---|---|
| `route_registry` (migration 010) | rota → execução: `entrypoint_node`, `owner`, `agente`, `cacheavel`, `permite_detour`, `doc_type`, `k`, `planner_steps` | Postgres + espelho Redis | Sim (`/hub/routes`, com versão + histórico + revert) |
| Capability Manifest (`capabilities/registry.py`, Fase 5) | tool → `descricao`, `interface`, `permissoes`, `confirmacao` | Código (`@tool`) + autodiscovery `pkgutil` | Binding agente↔tool é dado (`agente_tools`, migration 012) |
| `parser_factory._REGISTRY` | parser → builder lazy-import | Código; prioridade/enable é `dynamic_config` | Prioridade sim, conjunto não |

**O que falta** para fechar o que o pedido descreve:

1. **Nós não são unidades descritas.** Em `langgraph_experiment/nodes.py` os nós
   são funções soltas (`async def rag_node(state) -> dict`), importadas
   explicitamente por nome em `graph.py`. Não têm `display_name`, `description`,
   schema de input/output, categoria nem ícone. Não são descobríveis — só
   existem se alguém as importar.

2. **A topologia do grafo é hardcoded.** `langgraph_experiment/graph.py` são
   ~150 linhas de `graph.add_node(...)` / `graph.add_edge(...)` /
   `graph.add_conditional_edges(...)` repetitivas. Adicionar uma rota nativa
   nova = editar esse arquivo + `nodes.py` + o dict de `add_conditional_edges`
   do `classify` + (às vezes) `route_registry`. É o acoplamento que a §B do
   plano-mãe lista como problema.

3. **Não há manifesto de grafo.** O `langgraph.json` do LangGraph Studio mapeia
   `graphs: {"meu_agente": "./agente/graph.py:graph"}`. O Oráculo tem um grafo
   só, referenciado por import direto — sem registro, sem versão, sem "qual
   grafo está ativo".

> **Enquadramento:** a Fase 2 do plano-mãe se chama **"Route/Workflow
> Registry"**. Só a metade *Route* (registro de execução por rota) foi
> implementada. Esta proposta é a metade *Workflow* — a topologia do grafo
> como dado. Não é uma fase nova inventada; é o resto da Fase 2.

---

## B. Avaliação das referências enviadas

| Referência | O que é | Vale? | O que aproveitar |
|---|---|---|---|
| **Artigo LinkedIn** "zero-downtime plugin system for AI trading workflows" | Post de blog, sem código aberto. Descreve workflows em DB/JSON, plugins via `importlib`, grafo montado dinamicamente. | **Conceito sim, código não.** O framing "workflow como dado + discovery + grafo dinâmico" é justamente o que `route_registry` já faz para roteamento. Hot-reload sem downtime o Oráculo já resolve pelo espelho Redis + `hydrate_redis()`. | A ideia de **DB como fonte da topologia** (não um arquivo no disco) — encaixa no padrão Postgres+Redis que já existe. |
| **`langgraph.json` / LangGraph Studio** | Formato oficial. `graphs: {"id": "path.py:variável"}`, mapeando id → factory Python. | **Sim, diretamente.** É o "manifesto de grafo" que falta. | Um registro `graph_id → builder`, para ter "grafo ativo" nomeado, versionável, e permitir 2+ grafos coexistindo (ex.: um estável, um canário). |
| **Langflow components** | Cada nó é uma classe Python com inputs/outputs tipados; custom components estendem uma base. | **Sim, é o `BaseNode` do pedido.** Langflow é pesado (é uma plataforma visual inteira) — não adotar o framework, só o formato de componente. | O contrato **nó = classe com `inputs`/`outputs` declarados + `build()`/`execute()`**. Schema tipado (Pydantic). |
| **n8n custom nodes** | TypeScript. Objeto `description` com `displayName`, `name`, `group`, `inputs`, `outputs`, `properties`. | **Sim, como esquema de metadados.** A linguagem não importa; a forma do `description` importa. | Os campos do manifesto de nó: `name`, `display_name`, `description`, `group`/`category`, `icon`, `inputs`, `outputs`. |
| **`wb_json_to_langgraph` (workflowbuilder.io)** | Snippet Python: itera `nodes[]` → `add_node`, `edges[]` → `add_edge`. | **Sim, é o núcleo do adapter.** É literalmente `build_graph_from_spec`. | O laço `spec → StateGraph`, endurecido (validação, condicionais, subgrafos). |

**Veredito:** os conceitos são sólidos e usados em produção por vários
projetos. O que vale trazer é **o esquema de metadados** (Langflow/n8n) + **o
adapter spec→grafo** (workflowbuilder) + **o manifesto de grafo**
(`langgraph.json`). O que **não** vale: construir um editor visual, adotar
Langflow/n8n como dependência, ou suportar "N workflows arbitrários de
usuário" — o Oráculo tem um grafo, com evolução controlada pelo time.

---

## C. Vale a pena para o Oráculo? (custo × benefício honesto)

### A favor

- `graph.py` são 150 linhas repetitivas; a topologia viraria **dado**
  (editável no Hub como `route_registry`, versionada, com histórico e revert).
- Metadados de nó habilitam uma **visão de grafo no Hub** — a página
  `/hub/routes` (recém-migrada no Plano B) evoluiria de tabela para diagrama.
- Reduz o acoplamento "editar N arquivos para adicionar uma capacidade"
  (§B do plano-mãe): nó novo = 1 arquivo em `nodes/`, aparece sozinho.
- Testar um nó isolado fica trivial (contrato `execute(state, config) -> delta`
  explícito, sem precisar montar o grafo inteiro).

### Contra

- O Oráculo tem **um** grafo, não N workflows de usuário. O benefício
  "monte qualquer fluxo" não se aplica — ninguém além do time mexe nisso.
- Os **funis de ticket e CRUD** têm lógica de aresta condicional sutil
  (validadores por campo, `interrupt()` por pergunta, detecção de "sair"/RBAC
  que curto-circuita para `END`). Isso **não** reduz a `{source, target,
  condição}` sem perder expressividade — a condição é uma função Python com
  acesso ao `state`, não uma string.
- O bug conhecido do `langgraph-checkpoint-redis` com múltiplos `interrupt()`
  no mesmo nó (documentado em `dispatcher_langgraph.py`) forçou o desenho
  "1 nó por pergunta". Um esquema declarativo ingênuo convida a recriar esse
  problema.
- Indireção tem custo de leitura: hoje `graph.py` é chato mas **óbvio**. Um
  loader + spec + registry é mais peças para entender um grafo que muda de
  forma raramente.

### Recomendação: **adoção escopada**, em 3 camadas independentes

| Camada | O que | Vale? | Risco |
|---|---|---|---|
| **1. `BaseNode` + `NodeRegistry`** | Nós viram classes descritas, descobertas por `pkgutil` (mesmo padrão de `capabilities/registry.py`). | **Sim — maior valor, menor risco.** Metadados + testabilidade + descoberta, sem tocar a topologia. | Baixo. Refatoração mecânica de `nodes.py`. |
| **2. Spec declarativa para o fan-out simples** | O `classify → {rag, greeting, sigaa, check_status, media_download}` (arestas 1:1 e o roteador por `state.route`) vira JSON, guardado onde `route_registry` vive. | **Sim, condicional** — esse pedaço já é quase o que `route_registry` descreve. Unificar. | Médio. Precisa casar com `owner`/`FEATURE_LANGGRAPH_NATIVE_ROUTES`. |
| **3. Funis (ticket/CRUD) como subgrafos de código** | Cada funil continua definido em Python (`build_ticket_subgraph()`), registrado no `NodeRegistry` como **um** nó composto. A spec referencia `"type": "ticket_funnel"` e não conhece as arestas internas. | **Sim — não declarativizar o que é frágil.** | Baixo, é o estado atual encapsulado. |

Ou seja: **declarativo onde é simples e muda; código onde é sutil e estável.**

---

## D. Estrutura de pastas proposta

```
src/graph/                          # novo pacote (produção; langgraph_experiment/ é o protótipo)
  __init__.py
  state.py                          # OraculoState (movido de langgraph_experiment/state.py)
  base.py                           # BaseNode, NodeContext, NodeResult
  registry.py                       # @register_node, NodeRegistry, autodiscovery pkgutil
  spec.py                           # GraphSpec / NodeSpec / EdgeSpec (Pydantic) + validação
  builder.py                        # build_graph_from_spec(spec, registry, checkpointer) -> CompiledGraph
  loader.py                         # carrega a spec ativa (Postgres → Redis → default embutido)
  nodes/                            # um arquivo por nó; descoberto automaticamente
    __init__.py
    rag.py                          # RagNode(BaseNode)
    greeting.py
    check_status.py
    media_download.py
    sigaa.py
    classify.py
    ticket_funnel.py                # subgrafo de código, registrado como nó composto
    crud_funnel.py
  specs/
    default.json                    # topologia padrão, embutida — fallback se Postgres/Redis caírem
    default.schema.json             # JSON Schema da spec (para validar e para o editor do Hub)

migrations/versions/0XX_graph_spec.py   # tabela graph_spec + graph_spec_historico (espelha o padrão de route_registry)
```

`langgraph_experiment/` continua como está — protótipo/CLI. A migração porta
`nodes.py` → `src/graph/nodes/*.py` um nó por vez.

---

## E. Contratos (esboço de código)

### E.1 `base.py` — o nó

```python
from __future__ import annotations
from typing import ClassVar
from pydantic import BaseModel
from src.graph.state import OraculoState


class NodeIO(BaseModel):
    """Schema declarado de entrada/saída de um nó. Documentação + validação +
    material para a visão de grafo no Hub. NÃO é o state inteiro: são as chaves
    do state que o nó lê (inputs) e escreve (outputs)."""
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()


class BaseNode:
    # ── Manifesto (estático, lido pelo registry e pelo Hub) ────────────────
    name: ClassVar[str]                     # id único, snake_case — "rag", "greeting"
    display_name: ClassVar[str]             # "Busca RAG"
    description: ClassVar[str]              # 1 linha
    category: ClassVar[str] = "geral"       # "rag" | "hitl" | "sistema" | "integração"
    icon: ClassVar[str] = "circle"          # nome Lucide (casa com o set do Plano B)
    io: ClassVar[NodeIO] = NodeIO()

    # ── Execução ──────────────────────────────────────────────────────────
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    async def execute(self, state: OraculoState) -> dict:
        """Retorna o DELTA do state (dict), como os nós LangGraph atuais.
        `config` (do NodeSpec) já está em self.config."""
        raise NotImplementedError
```

Nó concreto (porta o `rag_node` atual):

```python
# src/graph/nodes/rag.py
from src.graph.base import BaseNode, NodeIO
from src.graph.registry import register_node


@register_node
class RagNode(BaseNode):
    name = "rag"
    display_name = "Busca RAG"
    description = "Recupera contexto no índice e sintetiza resposta ancorada."
    category = "rag"
    icon = "search"
    io = NodeIO(reads=("message", "rota", "history", "fatos"), writes=("answer",))

    async def execute(self, state):
        from langgraph_experiment.nodes import rag_node   # reaproveita a impl atual
        return await rag_node(state)
```

O funil de ticket entra como **nó composto** — a spec não vê as arestas
internas:

```python
# src/graph/nodes/ticket_funnel.py
@register_node
class TicketFunnelNode(BaseNode):
    name = "ticket_funnel"
    display_name = "Abertura de ticket (HITL)"
    description = "Funil guiado: tipo → categoria → queixa → confirmação → grava."
    category = "hitl"
    icon = "life-buoy"

    def as_subgraph(self, checkpointer):
        """Diferente dos nós simples: contribui um SUBGRAFO, não uma função.
        O builder detecta `as_subgraph` e usa add_node(name, subgraph)."""
        from src.graph.nodes._ticket_edges import build_ticket_subgraph
        return build_ticket_subgraph(checkpointer)
```

### E.2 `registry.py` — descoberta automática

Cópia fiel do padrão de `capabilities/registry.py` (que já funciona):

```python
from __future__ import annotations
import importlib, logging, pkgutil

logger = logging.getLogger(__name__)
_NODES: dict[str, type["BaseNode"]] = {}
_LOADED = False


def register_node(cls):
    if cls.name in _NODES:
        raise ValueError(f"nó duplicado: {cls.name}")
    _NODES[cls.name] = cls
    return cls


def _autodiscover() -> None:
    global _LOADED
    if _LOADED:
        return
    import src.graph.nodes as pkg
    for _, mod, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if not is_pkg and not mod.startswith("_"):
            try:
                importlib.import_module(f"src.graph.nodes.{mod}")
            except Exception as e:
                logger.error("❌ [GRAPH REGISTRY] falha ao importar %s: %s", mod, e)
    _LOADED = True


def get_node(name: str) -> type["BaseNode"]:
    _autodiscover()
    try:
        return _NODES[name]
    except KeyError:
        raise ValueError(f"nó '{name}' não registrado. Disponíveis: {sorted(_NODES)}")


def manifest() -> list[dict]:
    """Alimenta GET /hub/graph/nodes — catálogo para o editor/visão de grafo."""
    _autodiscover()
    return [
        {"name": c.name, "display_name": c.display_name, "description": c.description,
         "category": c.category, "icon": c.icon,
         "io": {"reads": list(c.io.reads), "writes": list(c.io.writes)}}
        for c in _NODES.values()
    ]
```

### E.3 `spec.py` — a definição declarativa

```python
from __future__ import annotations
from pydantic import BaseModel, Field


class NodeSpec(BaseModel):
    id: str                       # id da instância no grafo ("rag", "classify")
    type: str                     # name de um nó registrado
    config: dict = Field(default_factory=dict)


class EdgeSpec(BaseModel):
    source: str
    target: str                   # id de nó, ou "__end__"
    # aresta condicional: nome de um "router" registrado + mapa de retorno→destino
    when: str | None = None       # ex. "by_state_route"
    routes: dict[str, str] | None = None


class GraphSpec(BaseModel):
    version: int = 1
    entrypoint: str               # id do 1º nó após START
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]

    def validate_topology(self, registry) -> list[str]:
        """Erros que o Pydantic não pega: tipo inexistente, id órfão, destino
        de aresta desconhecido, nó inalcançável, ausência de caminho a __end__.
        Roda no POST do Hub ANTES de persistir (mesma disciplina de
        route_registry.validar_campos)."""
        ...
```

Os **routers** de aresta condicional (a parte que "não vira string") ficam num
pequeno registro de funções puras `state -> str`, versionadas com o código:

```python
# src/graph/routers.py
_ROUTERS: dict[str, callable] = {}

def router(name):
    def deco(fn): _ROUTERS[name] = fn; return fn
    return deco

@router("by_state_route")
def _by_state_route(state) -> str:
    return state.route            # "rag" | "ticket" | "crud" | "greeting" | ...
```

### E.4 `builder.py` — spec → `StateGraph`

```python
from langgraph.graph import END, START, StateGraph
from src.graph.state import OraculoState
from src.graph import registry, routers


def build_graph_from_spec(spec, checkpointer):
    spec_errs = spec.validate_topology(registry)
    if spec_errs:
        raise ValueError("spec de grafo inválida: " + "; ".join(spec_errs))

    g = StateGraph(OraculoState)

    for n in spec.nodes:
        NodeCls = registry.get_node(n.type)
        node = NodeCls(config=n.config)
        if hasattr(node, "as_subgraph"):
            g.add_node(n.id, node.as_subgraph(checkpointer))     # funis
        else:
            g.add_node(n.id, _instrumented(n.id, node.execute))  # mantém o Counter Prometheus atual

    g.add_edge(START, spec.entrypoint)
    for e in spec.edges:
        if e.when:
            fn = routers.get(e.when)
            mapping = {k: (END if v == "__end__" else v) for k, v in e.routes.items()}
            g.add_conditional_edges(e.source, fn, mapping)
        else:
            g.add_edge(e.source, END if e.target == "__end__" else e.target)

    return g.compile(checkpointer=checkpointer)
```

### E.5 `loader.py` — de onde vem a spec ativa

Mesmo padrão dos outros registries (Postgres fonte da verdade → espelho Redis →
default embutido se ambos caírem):

```python
def carregar_spec_ativa() -> GraphSpec:
    raw = _ler_redis("graph:spec:ativa")
    if raw is None:
        raw = _ler_postgres_e_reescrever_redis()      # read-repair
    if raw is None:
        raw = _default_embutido()                     # src/graph/specs/default.json
    return GraphSpec.model_validate(raw)
```

`hydrate_redis()` no startup do FastAPI e no `worker_process_init` do Celery —
idêntico ao que `dynamic_config` e `route_registry` já fazem em `main.py` e
`celery_app.py`.

---

## F. Exemplo de spec (a topologia atual, declarativa)

`src/graph/specs/default.json` — equivale ao que `graph.py` monta hoje para o
fan-out simples (funis referenciados como nós compostos):

```json
{
  "version": 1,
  "entrypoint": "classify",
  "nodes": [
    { "id": "classify",        "type": "classify" },
    { "id": "rag",             "type": "rag" },
    { "id": "greeting",        "type": "greeting" },
    { "id": "check_status",    "type": "check_status" },
    { "id": "media_download",  "type": "media_download" },
    { "id": "sigaa",           "type": "sigaa" },
    { "id": "ticket",          "type": "ticket_funnel" },
    { "id": "crud",            "type": "crud_funnel" }
  ],
  "edges": [
    {
      "source": "classify",
      "when": "by_state_route",
      "routes": {
        "rag": "rag", "greeting": "greeting", "check_status": "check_status",
        "media_download": "media_download", "sigaa": "sigaa",
        "ticket": "ticket", "crud": "crud"
      }
    },
    { "source": "rag",            "target": "__end__" },
    { "source": "greeting",       "target": "__end__" },
    { "source": "check_status",   "target": "__end__" },
    { "source": "media_download", "target": "__end__" },
    { "source": "sigaa",          "target": "__end__" },
    { "source": "ticket",         "target": "__end__" },
    { "source": "crud",           "target": "__end__" }
  ]
}
```

De 150 linhas de Python para ~40 de JSON, com o miolo frágil (arestas dos
funis) preservado em código.

---

## G. Relação com o `route_registry` (evitar dois cérebros)

Risco real: `route_registry.entrypoint_node` e a spec de grafo passariam a
descrever coisas que se sobrepõem. Regra de limite:

- **`route_registry`** responde: *dada a rota `CALENDARIO`, qual nó do grafo é
  o ponto de entrada, com que `doc_type`/`k`/`planner_steps`, e ela vai pro
  grafo ou pro `dispatcher.py` legado (`owner`)?* — é a ponte
  classificação→grafo.
- **`graph_spec`** responde: *uma vez dentro do grafo, quais nós existem e como
  se ligam?* — é a topologia interna.

O `classify_node` continua lendo `state.route` (posto lá pelo Supervisor via
`dispatcher_langgraph.py`). O `route_registry` não sai; ganha um "irmão" que
descreve o interior. A página `/hub/routes` cresce uma aba "Grafo".

---

## H. Onde isto entra no plano-mãe

É a **metade _Workflow_ da Fase 2** ("Route/Workflow Registry"), que ficou
pendente. Sugestão de numeração: **Fase 2-bis**, depois das Fases 1–5 (feitas)
e antes das Fases 6–8 (STT/TTS/channels/MCP, adiadas). Não compete com as
Fases 9–11 (multi-tenancy etc., condicionais a evento de negócio).

Pré-condição: as rotas nativas do grafo estarem estáveis em produção
(`FEATURE_LANGGRAPH_NATIVE_ROUTES`), o mesmo pré-requisito já registrado em
`aposentadoria_dispatcher_legado.md`. Enquanto o grafo é protótipo, mexer na
forma dele rende pouco.

---

## I. Migração incremental (sem big bang)

1. **`src/graph/` + `BaseNode` + `NodeRegistry`**, com `nodes/` reaproveitando
   as funções de `langgraph_experiment/nodes.py` por dentro (wrappers finos).
   `graph.py` continua sendo a fonte da topologia. Testes por nó.
2. **`spec.py` + `builder.py` + `specs/default.json`**. Um teste de equivalência:
   `build_graph_from_spec(default)` produz o mesmo grafo compilado que o
   `build_graph()` atual (mesmos nós, mesmas arestas). `graph.py` passa a
   chamar o builder com o default embutido.
3. **Migration `graph_spec` + `graph_spec_historico`** (espelha
   `009_config_dinamica` / `010_route_registry`: versão, `tenant_id` nulo,
   histórico append-only, revert). `loader.py` + `hydrate_redis()`.
4. **Hub**: `GET /hub/graph/nodes` (catálogo do registry) + `GET/POST
   /hub/graph/spec` (com `validate_topology` antes de gravar, 409 por versão) +
   aba/página que renderiza a spec como diagrama (read-only primeiro; editor
   depois, se houver demanda).
5. **Porta `nodes.py` de verdade** para `src/graph/nodes/*.py` (deixar de ser
   wrapper), um nó por PR. `langgraph_experiment/` vira só CLI de teste.

Cada passo é reversível e testável isolado. Passos 1–2 não tocam produção.

---

## J. Definição de "pronto"

- [ ] `NodeRegistry` descobre todos os nós de `src/graph/nodes/` sem import manual.
- [ ] Cada nó tem manifesto completo (`display_name`, `description`, `category`,
      `icon`, `io`) e teste unitário que roda o nó sem montar o grafo.
- [ ] `build_graph_from_spec(default.json)` é comprovadamente equivalente ao
      `build_graph()` atual (teste de topologia: conjunto de nós e arestas).
- [ ] `validate_topology` rejeita: tipo inexistente, id órfão, destino
      desconhecido, nó inalcançável, ausência de caminho a `__end__`.
- [ ] Spec persistida em Postgres, espelhada em Redis, com default embutido de
      fallback; `hydrate_redis` no startup da API e do worker.
- [ ] `POST /hub/graph/spec` valida antes de gravar, versiona, e tem revert —
      mesma disciplina de `route_registry`.
- [ ] Funis de ticket/CRUD continuam em código; a spec só os referencia.
- [ ] Um nó novo simples (ex.: `faq`) entra com **1 arquivo** em `nodes/` + 2
      linhas na spec, sem tocar `builder.py` nem `graph.py`.

---

## K. Riscos

| Risco | Mitigação |
|---|---|
| Recriar o bug do checkpointer Redis (múltiplos `interrupt()` por nó) via spec ingênua | Funis são subgrafos de código; a spec não expressa `interrupt()`. `validate_topology` não deixa um nó simples declarar múltiplos interrupts. |
| Dois cérebros: `route_registry` × `graph_spec` | Limite explícito (§G): registry = ponte classificação→grafo; spec = interior do grafo. Uma aba, um doc. |
| Over-engineering para um grafo que muda pouco | Adoção escopada (§C): camada 1 (registry) tem valor sozinha; camadas 2–3 só se a camada 1 provar que compensa. |
| `OraculoState` cresce e os `io.reads/writes` viram ficção | Teste que compara `io` declarado com o que o nó realmente acessa (análise estática simples do delta retornado). |
| Divergência entre `specs/default.json` e o que roda em produção (Postgres) | O default é só fallback de desastre; um teste garante que ele é uma spec válida e equivalente ao `build_graph()`. Produção sempre lê do Postgres/Redis. |

---

## L. Conclusão

O pedido é legítimo e alinhado com a direção que o plano-mãe já tomou — é
literalmente a metade que faltou da Fase 2. As referências valem pelos
**formatos** (metadados de nó estilo n8n/Langflow; adapter spec→grafo estilo
workflowbuilder; manifesto de grafo estilo `langgraph.json`), não pelos
frameworks.

A recomendação é **adoção escopada e faseada**: começar por `BaseNode` +
`NodeRegistry` (valor imediato, risco baixo, não toca produção), e só
declarativizar a topologia depois — e apenas o fan-out simples, mantendo os
funis HITL em código, porque a fragilidade deles (validadores, `interrupt()`,
checkpointer Redis) não cabe num `{source, target, condição}`.

Não construir editor visual nem suportar "workflows arbitrários de usuário": o
Oráculo tem um grafo, evoluído pelo time, e o ganho está em **descrever** e
**versionar** esse grafo — não em deixá-lo aberto.
