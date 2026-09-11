# O que é `src/graph_studio/` (e o que não é)

> Item C2.2 do plano de recuperação, 2026-09-09. Documento curto de propósito:
> a resposta cabe em duas frases, e o histórico de confusão em volta dela é
> justamente o motivo de existir este arquivo.

## A resposta curta

`src/graph_studio/` é uma **biblioteca de componentes de infraestrutura mais
um sandbox de desenho** para o Hub. **Não é o grafo de produção** e não tem
nenhum consumidor no caminho de uma mensagem real.

O grafo de produção é `src/application/orchestration/` — ver
[`graph-studio.md`](graph-studio.md), que apesar do nome parecido descreve a
`GraphSpec`, essa sim usada em produção.

## Como distinguir os dois

| | `application/orchestration/` | `graph_studio/` |
|---|---|---|
| O que é | O grafo que uma mensagem percorre | Componentes + canvas de rascunho |
| Vocabulário | `NodeSpec`, `EdgeSpec`, `GraphSpec` | `BaseNode`, `NodeRegistry`, `GraphExecutor` |
| Quem executa | `builder.build_graph()` → LangGraph | `graph_executor.py`, atrás de flag desligada |
| Onde aparece no Hub | Aba "Grafo de produção" | Aba "Sandbox (experimental)", `/hub/graph-nodes` |
| Afeta o bot? | **Sim** | **Não** |

## Por que existe

Veio da ideia de uma "Camada 1" de nós declarativos, inspirada no LangGraph
Studio e em arquiteturas de plugin: descobrir nós automaticamente, descrevê-los
por metadados, montar topologias por dado. A intenção era legítima.

O que aconteceu foi que o problema de "topologia como dado" acabou resolvido
do outro lado, em `orchestration/spec.py` (ADR 0008 Fase 5), com uma
`GraphSpec` validada e routers versionados em código. Restaram **dois motores
de grafo**, e só um com usuários.

## Decisão: congelado

Congelado significa: **continua funcionando, não recebe investimento novo.**

- Não remover. Alimenta a aba de sandbox e o catálogo `/hub/graph-nodes`, e
  remover é trabalho sem retorno.
- Não construir em cima. Funcionalidade nova de fluxo vai para
  `orchestration/`.
- `FEATURE_GRAPH_EXECUTOR_PILOTO` e as tabelas `graph_topology` e
  `graph_node_config` ficam congeladas junto.

Registrado como TD-020. Estado geral: [`../ESTADO_ATUAL.md`](../ESTADO_ATUAL.md).

## O enquadramento que saiu de circulação

Os documentos que apresentavam isto como "Camada 1" e como caminho para o
futuro do produto foram arquivados em
`docs/historico/roadmap-2026-superado/`, com banner. O `.md` que dizia que a
arquitetura de nós declarativa "não foi codificada" foi corrigido: ela foi,
só que do outro lado.
