# Graph Studio — o grafo de produção como dado

> ADR 0008 Fases 4/5. A topologia do grafo de orquestração (o que uma
> mensagem percorre depois de classificada) deixou de ser código hardcoded e
> virou uma `GraphSpec` versionada — editável pelo Hub, com histórico e
> revert, como o `route_registry` e a config dinâmica.

## As duas abas

| Aba | O que é | Afeta produção? |
|---|---|---|
| **Grafo de produção** | A `GraphSpec` ATIVA. Diagrama read-only do fluxo real + criar/remover *rotas terminais* + histórico/revert. | **Sim** — no próximo restart dos workers. |
| **Laboratório** | Canvas livre com os componentes de infraestrutura (`src/graph_studio/`: LLM, STT, busca…). Rascunho e teste de peças isoladas. | Não. Nunca foi o grafo de produção. |

## O que é a `GraphSpec`

```
GraphSpec = { version, entrypoint, nodes[], edges[] }
  NodeSpec = { id, type, config, locked }
  EdgeSpec = { source, target, when?, route_value?, locked }
```

- `type` referencia um dos 16 tipos do **manifesto** (`orchestration/node_manifest.py`) —
  cada tipo tem rótulo, descrição, categoria, ícone e as chaves do state que
  lê/escreve.
- Aresta simples: `{source, target}`. Aresta condicional: `{source, when, route_value, target}` —
  `when` é o nome de um **router** (`orchestration/routers.py`), uma função
  `state -> str` versionada com o código (a lógica da aresta **não** vira
  string). Arestas condicionais que saem do mesmo nó viram um único
  `add_conditional_edges`.
- Fonte da spec ativa: **Redis → Postgres (`graph_spec`) → `specs/default.json`**
  embutido. O `default.json` é a topologia de hoje; um teste
  (`test_spec_default_equivale_a_baseline`) prova que ele compila para
  exatamente o mesmo grafo que a versão hardcoded anterior.

## Por que os funis são `locked`

Os funis de ticket e CRUD (`ticket_ask_*`, `crud_ask_*`) ficam com
`locked: true`. A GUI os mostra mas não deixa editar, porque a fragilidade
deles não cabe num `{source, target}`:

- validador por campo com re-pergunta (`_tipo_valido`, `_categoria_valida`, …);
- um `interrupt()` por pergunta — o bug conhecido do `langgraph-checkpoint-redis`
  com múltiplos interrupts pendentes no mesmo nó forçou o desenho "1 nó por
  pergunta";
- detecção de "sair"/RBAC que curto-circuita pro `__end__`.

`validate_topology()` roda no POST antes de gravar e rejeita: tipo inexistente,
id órfão, target desconhecido, router não registrado, nó inalcançável, nó sem
caminho até `__end__`, mistura de aresta condicional e simples no mesmo nó.

## Criar um fluxo novo (o caso de uso da GUI)

Um "fluxo novo" = uma **rota terminal** nova. Ex.: uma rota `FAQ_BIBLIOTECA`
que responde perguntas sobre horário/funcionamento da biblioteca com um RAG
filtrado na wiki.

Na aba **Grafo de produção → Criar um fluxo novo**:

1. **Nome da rota** — `FAQ_BIBLIOTECA` (MAIÚSCULAS, 3–24 caracteres).
2. **Tipo de nó** — `Responder com base nos documentos` (tipo `rag`). Os
   tipos adicionáveis são as rotas terminais (`rag`, `check_status`,
   `greeting`, `media_download`, `sigaa`, `human_handoff`); na prática só
   `rag` faz sentido pra um fluxo de conteúdo novo.
3. **Gatilho** — a regex/frase que o classificador casa pra mandar a mensagem
   pra essa rota: `horário da biblioteca|funcionamento da biblioteca`.
4. **Tipo de documento / k / cachear** — parâmetros do RAG (taxonomia e
   profundidade de retrieval).
5. **Criar fluxo.**

O que acontece numa transação só:

- `route_registry`: linha nova `FAQ_BIBLIOTECA` com `entrypoint_node="faq_biblioteca"`
  (id derivado do nome), `doc_type`, `k`, `cacheavel`, `owner="langgraph"`.
- `intents_router` + espelho Redis (`router:regex`/`router:config`): o gatilho
  — o classificador passa a reconhecer a rota **na próxima mensagem**, sem
  restart.
- `graph_spec`: nó `{id: "faq_biblioteca", type: "rag", config: {...}}` +
  aresta `classify --route_value="faq_biblioteca"--> faq_biblioteca` +
  aresta `faq_biblioteca --> __end__`. Versão nova, histórico, espelho Redis.

**O nó novo do grafo só entra em vigor no próximo restart dos workers**
(o grafo é compilado uma vez por processo). O gatilho de classificação e a
config da rota valem na hora.

## Remover, histórico, reverter

- **Remover** um fluxo personalizado desfaz as três coisas (nó + arestas da
  spec, linha do `route_registry`, gatilho). Os fluxos fixos não podem ser
  removidos.
- **Histórico** lista todas as versões da `graph_spec` com quem/quando.
  **Reverter** grava o snapshot de uma versão antiga como versão nova
  (reversível). O snapshot antigo é revalidado contra o manifesto atual antes
  de aplicar.

## Onde fica o quê

| Peça | Arquivo |
|---|---|
| Modelos + `validate_topology` | `src/application/orchestration/spec.py` |
| Manifesto dos tipos de nó | `src/application/orchestration/node_manifest.py` |
| Routers de aresta condicional | `src/application/orchestration/routers.py` |
| `spec → StateGraph` | `src/application/orchestration/builder.py::build_graph_from_spec` |
| Spec ativa (Redis→PG→default) | `src/application/orchestration/loader.py` |
| Edições de alto nível (add/remove rota) | `src/application/orchestration/spec_editor.py` |
| Topologia default embutida | `src/application/orchestration/specs/default.json` |
| Persistência + histórico + revert | `src/infrastructure/repositories/graph_spec_repository.py` (migration 024) |
| Endpoints do Hub | `src/api/routers/web/hub.py` (`/hub/graph-studio/spec*`) |
| Front | `templates/hub/graph-studio.html` + `static/js/pages/graph-spec.js` |
