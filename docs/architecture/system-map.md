# Mapa do Sistema — Oráculo UEMA

> Ponto de partida rápido: "onde fica X?". Para o *porquê* e o fluxo
> detalhado, ver [`arquitetura_oraculo.md`](arquitetura_oraculo.md). Este
> documento não duplica conteúdo — só aponta.

| Preciso encontrar... | Está em |
|---|---|
| **Entrada da API / webhook** | `src/main.py`, `src/application/webhook/` |
| **Pré-filtro de mensagem (gate)** | `src/router/gatekeeper.py` |
| **Menu do bot (quem decide a rota no v1)** | `src/application/menu/` (`resolver.py` decide, `spec.py` valida, `state.py` guarda a posição, `menus/default.json` é o conteúdo embutido) |
| **Editar o texto do menu sem deploy** | `/hub/menu` → `templates/hub/menu.html`, `static/js/pages/menu.js`, `repositories/menu_config_repository.py`, tabela `menu_config` (migration 025) |
| **Classificação de intenção (Supervisor)** | `src/router/supervisor.py`, `src/router/llm_fallback.py` — **fora do caminho crítico no v1** |
| **Orquestração (entrypoint único, ADR 0008)** | `src/application/orchestration/entrypoint.py` |
| **Grafo de produção (nós, arestas)** | `src/application/orchestration/builder.py`, `nodes.py`, `state.py` |
| **Topologia do grafo como dado (GraphSpec)** | `src/application/orchestration/spec.py`, `specs/default.json`, `loader.py` — ver `docs/architecture/graph-studio.md` |
| **RAG — busca e síntese (o domínio do v1)** | `src/rag/knowledge/` (`service.py` busca, `synthesis.py` responde) |
| **Domínios fora do v1** | `src/domain_services/` (`sigaa/`, `tickets/`, `conversation/`) |
| **Nomes de agente (para o kill-switch)** | `src/domain/agentes.py` — conjunto fechado; a descrição editável está em `agentes_catalogo` |
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
  → Celery (worker, fila default) → gatekeeper.py
  → orchestration/entrypoint.py → application/menu (decide a rota, 0 token)
  → StateGraph (orchestration/builder.py) → rag/knowledge · domain_services
  → llm_factory.py → Evolution API → WhatsApp
```

No v1, navegar o menu, ler um texto fixo e pedir atendente **param no
entrypoint** e nunca chegam ao grafo. Ver [`../ESTADO_ATUAL.md`](../ESTADO_ATUAL.md).

Detalhe completo de cada seta: [`arquitetura_oraculo.md` §5](arquitetura_oraculo.md).
