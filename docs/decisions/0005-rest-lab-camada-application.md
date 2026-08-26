# ADR 0005 — REST lab ganha camada de Application, continua sendo laboratório de estudo

- **Status:** ativo
- **Data:** 2026-08-25
- **Fonte:** Decisão 03 do plano de integração LangGraph/REST/MCP (Fase 3)

## Contexto

`rest_lab/` nasceu como laboratório isolado pra estudar integração REST
(`httpx`) contra APIs públicas de terceiros (JSONPlaceholder, DummyJSON,
httpbin) — nunca foi uma API real da UEMA, nunca tocou banco/Redis/
infraestrutura de produção, e continua sem tocar. A auditoria de 2026-08-24
confirmou: `rest_lab/router.py` (regex de comando, prefixo `"rest "`) →
`rest_lab/tools.py` chamava `httpx` **direto**, sem passar por nenhum caso
de uso — divergente do padrão do resto do projeto
(`src/application/use_cases/`).

## Decisão

Introduzir `src/application/use_cases/rest_lab_use_case.py::RestLabUseCase`
como a camada de Application que faltava. `rest_lab/tools.py` vira um
facade fino (mesmas assinaturas de sempre, delega pro use case) —
`rest_lab/router.py` e `rest_lab/run_test.py` não mudaram nenhuma linha.
`rest_lab/clients.py` (os clientes `httpx` lazy por API) continua onde
está — é infraestrutura de fato, só reaproveitada pelo use case em vez de
por `tools.py` direto.

**O que NÃO mudou:** `rest_lab` continua sendo um laboratório de estudo,
gateado por prefixo `"rest "`, interceptado em
`dispatcher_langgraph.py::processar()` antes de qualquer outra coisa (nota
existente naquele módulo). Não vira uma "API REST oficial da UEMA" — a
decisão de formalizar a camada de Application não implica formalizar o
*conteúdo* (as três APIs públicas de terceiros continuam sendo o que
sempre foram: material de estudo).

**Sobre `FEATURE_REST_PRODUCT`** (flag criada na Fase 1 do plano,
`settings.py`): não guardou nenhum comportamento nesta fase — não existe
um "antes/depois" comportamental pra gatear aqui (o refactor é 100%
transparente: mesma entrada, mesma saída, testado em
`tests/unit/application/test_rest_lab_use_case.py`). A flag continua
declarada e desligada por padrão, reservada caso uma decisão futura
precise de um corte real de comportamento nesta área.

## Consequências

- `rest_lab/tools.py` deixa de ser o dono da lógica de chamada HTTP —
  qualquer mudança futura na integração REST acontece em
  `RestLabUseCase`, não espalhada entre router/tools/clients.
- Primeira cobertura de teste automatizado que `rest_lab/` já teve (13
  testes, mockando `rest_lab.clients.get_client` — antes desta fase,
  `rest_lab` nunca tinha sido testado sem rede real).
- Nenhum risco de regressão pro fluxo de produção: `rest_lab` continua
  isolado, sem consumidor fora de `dispatcher_langgraph.py`/`run_test.py`.
