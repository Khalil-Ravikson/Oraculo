# Mapa do Sistema — Oráculo UEMA

> Ponto de partida rápido: "onde fica X?". Para o *porquê* e o fluxo
> detalhado, ver [`arquitetura_oraculo.md`](arquitetura_oraculo.md). Este
> documento não duplica conteúdo — só aponta.

| Preciso encontrar... | Está em |
|---|---|
| **Entrada da API / webhook** | `src/main.py`, `src/application/webhook/` |
| **Pré-filtro de mensagem (gate)** | `src/router/gatekeeper.py` |
| **Classificação de intenção (Supervisor)** | `src/router/supervisor.py`, `src/router/llm_fallback.py` |
| **Orquestração (entrypoint único, ADR 0008)** | `src/application/orchestration/entrypoint.py` |
| **Grafo de produção (nós, arestas)** | `src/application/orchestration/builder.py`, `nodes.py`, `state.py` |
| **Agentes de domínio** | `src/agents/` (`academic_knowledge/` RAG, `sigaa/`, `tickets/`, `conversation/`) |
| **Tools/integrações autodescobertas** | `src/capabilities/` (`rag/`, `sigaa/`, `messaging/`, `persistence/`, `tools/`) |
| **RBAC / permissões** | `src/domain/permissions.py` |
| **Filas e workers Celery** | `src/infrastructure/celery_app.py` (config), `docker-compose.yml` (containers) |
| **RAG — ingestão de documentos** | `src/rag/ingestion/` (`pipeline.py`, `parser_factory.py`, `chunker_factory.py`) |
| **RAG — busca híbrida** | `src/application/use_cases/retrieve_context_use_case.py`, `src/infrastructure/redis_client.py` |
| **Memória (trabalho / longo prazo / identidade)** | `src/memory/` (`ports/`, `adapters/`, `services/`) |
| **Providers de LLM (Gemini/DeepSeek/Groq)** | `src/infrastructure/adapters/llm_factory.py`, `gemini_provider.py`, `openai_compatible_provider.py` |
| **STT (voz → texto)** | `src/infrastructure/adapters/gemini_stt_provider.py`, `src/infrastructure/services/audio_service.py` |
| **TTS (texto → voz)** | `src/infrastructure/adapters/kokoro_tts_provider.py` |
| **Observabilidade (custo, métricas, tracing)** | `src/infrastructure/observability/` (`metrics.py`, `pricing.py`, `tracing.py`) |
| **Banco relacional (Postgres)** | `src/infrastructure/database/`, `migrations/` (Alembic) |
| **Cache/estado (Redis)** | `src/infrastructure/redis_client.py`, `src/infrastructure/semantic_cache.py` |
| **Portal admin (`/hub`)** | `src/api/routers/web/hub.py`, `templates/hub/` |
| **API admin (JSON)** | `src/api/routers/admin/` (`admin_api.py`, `eval_api.py`, `admin_users_api.py`) |
| **Configuração (env vars)** | `src/infrastructure/settings.py`, `.env.example` |
| **Testes** | `tests/unit/` (espelha `src/`), `tests/integration/`, `tests/e2e/`, `tests/eval/` |
| **Laboratórios de pesquisa (não produção)** | `rest_lab/`, `mcp_lab/` — ver banner em cada diretório |
| **Graph Studio (paleta de componentes + sandbox, não é o grafo de produção)** | `src/graph_studio/`, `templates/hub/graph-studio.html` |

## Fluxo de uma mensagem, em uma linha por etapa

```
Evolution API → webhook FastAPI → Porteiro (Postgres) → lock (Redis)
  → Celery (worker, fila default) → gatekeeper.py → router/supervisor.py
  → orchestration/entrypoint.py → StateGraph (orchestration/builder.py)
  → agents/capabilities → llm_factory.py → Evolution API → WhatsApp
```

Detalhe completo de cada seta: [`arquitetura_oraculo.md` §5](arquitetura_oraculo.md).
