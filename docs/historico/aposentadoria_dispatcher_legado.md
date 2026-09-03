# Aposentadoria do `dispatcher.py` legado — checklist de rollout

> **Status: decisão fechada (ADR 0008), execução gated.** As Fases 0-2 do
> ADR 0008 fecharam os bloqueios de código #2 e #4. Falta só o bloqueio #1
> (janela de validação em WhatsApp real com `FEATURE_LANGGRAPH_NATIVE_ROUTES`
> ligada) — quando isso rodar N dias sem regressão, a Fase 3 flipa as 4 rotas
> pra `owner="langgraph"` e deleta `dispatcher.py` + Planner.

## Contexto

`src/application/runtime/dispatcher.py` (legado, ex-`cognitive_os.py`) e
`src/application/runtime/dispatcher_langgraph.py` (produção, ADR 0001
"substituído" / Decisão 01 do plano de integração) coexistem. TD-001.

`dispatcher_langgraph.py` é o entry point real (`process_message_task.py`
importa `processar` dele incondicionalmente). Ele **delega** para
`dispatcher.py::processar` toda rota cujo `route_registry.owner` seja
`legacy` ou `langgraph_conditional` com `FEATURE_LANGGRAPH_NATIVE_ROUTES`
desligada — hoje: `SIGAA`, `MEDIA_DOWNLOAD`, `GREETING`, `CHECK_STATUS`
(a flag está `false` em produção).

## O que a Fase 2 fez (pré-requisitos — Parte C)

1. **`OSResult` extraído** para `src/application/runtime/contracts.py`.
   `agents/sigaa/auth_flow.py`, `agents/tickets/ticket_flow.py`,
   `agents/tickets/crud_tool.py`, `dispatcher_langgraph.py` deixam de importar
   o contrato do dispatcher legado. `dispatcher.py` re-exporta para
   compatibilidade.
2. **Ingestão de áudio extraída** para `src/application/runtime/audio_intake.py`
   (`_transcrever_audio_recebido`, `_quer_resposta_em_audio`,
   `_remover_pedido_audio` + constantes). `dispatcher_langgraph.py` e
   `process_message_task.py` deixam de importar do dispatcher legado.
3. **Callers de debug migrados** para `dispatcher_langgraph.processar`:
   - `src/api/routers/web/hub.py` (`POST /eval/query`) — lê só `.answer`/`.rota`, drop-in.
   - `src/api/routers/admin/eval_api.py` (`_evaluate_single`) — mantém o
     fallback `_aguardar_resposta_final` (que continua em `dispatcher.py`,
     ainda importável); a eval passa a rodar pelo caminho de produção.
   - `src/api/chain_sse.py` — import trocado por consistência, **mas o
     `sse_router` não está registrado em `main.py`** (código órfão — ver
     recomendação abaixo).

Depois disso, os únicos consumidores de `dispatcher.py` são:
`dispatcher_langgraph.py` (delegação intencional) e `process_message_task.py`
(nada — só via `dispatcher_langgraph`).

## O que falta para efetivamente aposentar

| # | Bloqueio | Tipo | Estado (ADR 0008) |
|---|---|---|---|
| 1 | `FEATURE_LANGGRAPH_NATIVE_ROUTES=true` nunca validado via WhatsApp real. Enquanto `false`, `dispatcher.py` é caminho de produção ativo para SIGAA/MEDIA_DOWNLOAD/GREETING/CHECK_STATUS. | **Rollout** | ⏳ **ABERTO** — é o único gargalo restante. Operação. |
| 2 | Os nodes nativos não rodam circuit-breaker por agente nem semantic cache — `dispatcher.py` roda. | Código | ✅ **FECHADO** (Fase 1). Circuit-breaker movido pro `entrypoint.py`, roda pra TODAS as rotas antes de delegar/entrar no grafo. Semantic cache: as 4 rotas condicionais são `cacheavel=False` — não havia cache pra replicar. |
| 3 | Fluxo do Planner (`criar_plano` → `_despachar_workers`) só existe em `dispatcher.py`. | Código / avaliação | Cenário A (RAG chord) já replicado por `_responder_rag_via_celery`. Cenário B (`_plano_sigaa`/`_plano_media`) fica sem chamador assim que a flag ligar (`sigaa_node`/`media_download_node` montam a própria chain). Deletar junto com `dispatcher.py`. |
| 4 | `chain_sse.py` — órfão. | Limpeza | ✅ **FECHADO** (Fase 0). Deletado. |
| 5 | `_aguardar_resposta_final` (usado por `eval_api.py`). | Config | Dispensável quando `FEATURE_LANGGRAPH_CELERY_DISPATCH` estiver garantidamente ligada (hoje `true` no `.env`). Deletar junto com `dispatcher.py`. |

## Sequência de rollout (Fase 3 do ADR 0008)

1. `.env` de homologação: `FEATURE_LANGGRAPH_NATIVE_ROUTES=true` (exige
   restart — é `settings.py`, não config dinâmica). Sem tocar `route_registry`.
2. Validar por WhatsApp real, N dias: GREETING, CHECK_STATUS, SIGAA (login
   CPF/senha completo), MEDIA_DOWNLOAD (link + busca por termo). Comparar com
   o comportamento de `FEATURE_LANGGRAPH_NATIVE_ROUTES=false`.
3. Sem regressão: migration que muda `route_registry` das 4 rotas de
   `langgraph_conditional` → `langgraph` + `_DEFAULTS` + `OWNERS_VALIDOS`
   → `{"langgraph"}`.
4. Deletar: `dispatcher.py`, `application/chain/planner.py` (shim),
   `criar_plano`/`_planejar_com_pro`/`_plano_*`, `_despachar_workers`,
   `_aguardar_resposta_final`, `_buscar_resposta_cached`. Reescrever
   `eval_api.py:314-325` pra ler `.answer` direto. Deletar
   `worker_greeting`/`worker_action` (Planner-only). Deletar
   `tests/unit/application/test_dispatcher*.py`.
