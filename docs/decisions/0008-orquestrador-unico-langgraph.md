# ADR 0008 — Orquestrador único de mensagem sobre o StateGraph do LangGraph

- **Status:** ativo (em execução faseada)
- **Data:** 2026-09-02
- **Supera:** ADR 0001 (LangGraph aprovado como dispatcher definitivo — este
  ADR fecha a migração que o 0001 abriu). Complementa ADR 0007 (Hub v2).
- **Fonte:** plano `~/.claude/plans/voce-consegue-fazer-um-polished-salamander.md`,
  construído sobre `docs/historico/aposentadoria_dispatcher_legado.md` e
  `docs/historico/arquitetura_nos_declarativa.md`.

## Contexto

O processamento de mensagem estava espalhado por **dois orquestradores**
(`application/runtime/dispatcher.py` legado + `dispatcher_langgraph.py`) e
**três vocabulários de "nó"** (`route_registry.NODES_ENTRYPOINT`,
`src/graph/` do Hub v2, `reference_flows.py`). Isso é TD-001/TD-002/TD-013 e
causou 2 incidentes reais (nota de voz virando embedding vazio;
reclassificação dupla paga no Gemini Flash). Faltava também um caminho de
escalonamento para atendente humano.

## Decisão

1. **Um entrypoint único** em `src/application/orchestration/entrypoint.py`
   (ex-`dispatcher_langgraph.py`), sobre o `StateGraph` montado por
   `orchestration/builder.py` (ex-`langgraph_experiment/graph.py`).
   `langgraph_experiment/` deixa de existir.
2. **`src/graph/` → `src/graph_studio/`** — é biblioteca de componentes de
   infraestrutura + sandbox do Graph Studio, nunca foi o grafo de produção.
3. **Circuit-breaker por agente no entrypoint**, valendo para todas as rotas
   (antes só `dispatcher.py`, não valia para as rotas nativas do grafo).
4. **Rota `ESCALAR_HUMANO` + nó terminal `human_handoff`** — silencia o bot
   para a sessão (Redis `handoff:session:{id}`, TTL 24h), enfileira em
   `handoff:queue`, avisa `settings.SUPPORT_GROUP_JID`. Sai com `$voltar`.
5. **`dispatcher.py` e o Planner são aposentados** quando
   `FEATURE_LANGGRAPH_NATIVE_ROUTES` estiver validada em produção — as 4
   rotas condicionais (GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS) passam a
   `owner="langgraph"` e o legado é deletado.
6. **Topologia do grafo como dado** (`GraphSpec`, Fase 5) — finaliza a
   metade "Workflow" da Fase 2 do Plano A. Funis de ticket/CRUD ficam em
   código (subgrafos); só o fan-out simples vira spec.
7. **Hub realinhado** para mostrar um só grafo — o de produção real, gerado
   de `orchestration.builder.describe()`.

## Faseamento

| Fase | O que | Estado |
|---|---|---|
| 0 | Pacote `orchestration/` + `src/graph_studio/` + limpeza | ✅ `33dbf25` |
| 1 | Entrypoint único + circuit-breaker global + TD-013 | ✅ `a7f3d31` |
| 2 | Rota/nó `human_handoff` (ESCALAR_HUMANO) + migration 022 | ✅ `b988ab2` |
| 3 | Aposentar `dispatcher.py` + Planner | ⏳ **gated** — bloqueio #1 (rollout) |
| 4 | Realinhar o Hub | pendente |
| 5 | `GraphSpec` declarativa | pendente |

## Consequências

- Toda mudança de roteamento passa a ter **um lugar** (`orchestration/`) —
  fim de "verifique os dois dispatchers".
- O kill-switch de `/hub/agents` passa a valer para SIGAA, RAG e todas as
  rotas nativas — antes só para as delegadas.
- Enquanto a Fase 3 não fecha, `dispatcher.py` continua sendo o caminho de
  produção de GREETING/SIGAA/MEDIA_DOWNLOAD/CHECK_STATUS (comportamento
  idêntico ao de antes, `FEATURE_LANGGRAPH_NATIVE_ROUTES=False`).
- A Fase 3 depende de uma **janela de validação em WhatsApp real** com a flag
  ligada — é rollout, não código (ver `aposentadoria_dispatcher_legado.md`).

## Dívidas fechadas

- **TD-013** (Fase 1) — override `IGNORE→LLM` do gatekeeper deixa de ser
  incondicional.
- **TD-017** (Fase 3) — teste de auth SIGAA reescrito para o `auth_token`.
- **TD-018** (Fase 3) — testes de cadastro forçam `DEV_TEST_NO_DB_WRITE=False`.
- **TD-012 / TD-014** (Fase 3) — 5 arquivos de teste órfãos deletados.
- **TD-001 / TD-002** — fecham quando a Fase 3 deletar `dispatcher.py`.
