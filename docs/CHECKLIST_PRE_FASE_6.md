# Checklist Pré-Fase 6: O que validar antes de começar

> **Objetivo**: Garantir que o terreno está sólido antes de meter a picareta.

---

## ✅ Validações do Projeto Existente (Plano A – Fases 1–5)

### Infraestrutura de Config Dinâmica (Fase 1)

- [ ] `src/infrastructure/dynamic_config.py` — `get_bool`, `get_int`, `get_str` funcionando?
- [ ] Coluna `versao` em `config_dinamica` — incrementada em toda escrita?
- [ ] Read-repair no miss Redis → Postgres → reescreve Redis? (teste manual)
- [ ] Tabela `config_dinamica_historico` — registra old_value/new_value/versao?
- [ ] `/hub/config` tem botão "reverter pra esta versão"?
- [ ] Teste de concorrência: duas escritas simultâneas na mesma chave retornam conflito HTTP 409?
- [ ] Teste de degradação: Postgres fora → cai pro default sem exceção? Redis fora → idem?

### Route Registry (Fase 2)

- [ ] `src/infrastructure/route_registry.py` — todas as rotas vivas em Postgres?
- [ ] `/hub/routes` mostra registro + status + histórico?
- [ ] Nova rota criada via Hub chega no Graph certo sem editar `dispatcher_langgraph.py`?
- [ ] Zero regressão nas 11 rotas atuais?

### LLM Provider Registry + Circuit Breaker (Fase 3)

- [ ] `src/infrastructure/adapters/llm_provider_registry.py` — dict de builders?
- [ ] `llm_circuit_breaker.py` — abre após ~5 falhas consecutivas?
- [ ] Período de resfriamento ~60s (half-open recovery)?
- [ ] Alerta quando abre (não muda provider automaticamente)?
- [ ] Fallback funciona?

### Parser Registry (Fase 4)

- [ ] `parser_factory.py::auto()` — lê prioridade de config dinâmica?
- [ ] Novo parser pode ser habilitado/desabilitado sem restart?
- [ ] Fallback em cadeia mantém ordem de prioridade?

### Tool Registry (Fase 5)

- [ ] `src/capabilities/registry.py` — revivido com decorator?
- [ ] Manifesto de capability em cada tool (versão de interface)?
- [ ] Tabela `agente_tools` (junção nó↔tool) — existe e é usada?
- [ ] `/hub/capabilities` mostra tools associados a cada agente?

---

## ✅ Validações do Plano B (Frontend – Fases 0–5)

### Design System

- [ ] `static/css/tokens.css` — paleta única (ink-950, ink-900, signal, ok, warn, danger)?
- [ ] `static/css/base.css` — reset + tipografia (Geist Sans + Geist Mono)?
- [ ] `static/css/layout.css` — sidebar (~72px / 220px) + topbar + grid 12 colunas?
- [ ] `static/css/components/*.css` — card, table, button, badge, form, modal, toast?

### Templates

- [ ] `templates/hub/_shell.html` — sidebar + topbar + blocos `content` / `extra_css` / `extra_js`?
- [ ] Todas as 14 rotas estendem `_shell.html`?
- [ ] Legado removido: `_base.html`, `hub-bridge.css`, `hub.css`, `hub_index.css`?

### Frontend Auditorias

- [ ] Zero `<style>` inline? (grep -c `<style>` templates/hub/*.html → 0)
- [ ] Zero `<script>` inline? (grep -c `<script>` templates/hub/*.html → 0)
- [ ] Zero `on*=` (onclick, oninput)? (grep -c `on[a-z]*=` templates/hub/*.html → 0)
- [ ] Zero emoji? (grep -c `[😀-🿿]` templates/hub/*.html → 0)
- [ ] Todos os ícones são SVG (Lucide)?
- [ ] Favicon existe (`<link rel="icon">` em `_shell.html`)?
- [ ] `<title>` segue padrão `{Página} — Oráculo UEMA`?
- [ ] Foco de teclado visível (`:focus-visible` em `form.css`)?

### Manual Sign-Off

- [ ] Abrir `localhost:9000/hub/` no navegador.
- [ ] Navegar pelas 14 páginas (index, config, agents, users, audit, etc).
- [ ] Verificar zero erro no console.
- [ ] Testar em 375px (mobile) — sem overflow horizontal.
- [ ] Revisar especialmente: `chunkviz.html` (maior refactor JS), `chat.html` (novo design).

---

## ✅ Pré-Requisitos Técnicos para Camada 1

### LangGraph em Main

- [ ] `FEATURE_LANGGRAPH_NATIVE_ROUTES=true` está em config dinâmica?
- [ ] Branch `arquitetura/plataforma-orientada-a-configuracao` (atual) é work-in-progress pra Camada 1?
- [ ] LangGraph issues resolvidas: event loop, resumption dupla de `interrupt()` (ver `.claude.md` linha 14-16)?

### RBAC Testado em Main

- [ ] `domain/permissions.py::_PERMISSOES` — dict Python com roles/perms?
- [ ] RBAC checado em nodes de entrada (`.claude.md` linha 14, item "9.2")?
- [ ] Testes de RBAC rodando verde em CI? (se CI existir)

### Tool Calling Nativo (pré-req de Fase 8)

- [ ] `google.genai` com `bind_tools()` implementado?
- [ ] Código morto `gmail_tool.py` removido ou atualizado?
- [ ] Diferenciação clara: `response_schema` Pydantic (hoje) vs. `bind_tools` (futuro)?

---

## ✅ Infraestrutura de Testes

### Estrutura de Testes Existente

- [ ] `tests/unit/` — testes unitários de config, registry, providers?
- [ ] `tests/integration/` — testes com banco de dados real?
- [ ] `tests/eval/` — golden dataset (fixtures congeladas)?
- [ ] CI/CD com GitHub Actions? (ver `.github/workflows/`)
  - Se SIM: [ ] `pytest tests/unit tests/eval` roda em cada PR?
  - Se NÃO: [ ] Plano pra CI existe? (Plano A Fase 0/1 menciona isso)

### Testes de Config Dinâmica (modelo pra Camada 1)

- [ ] `test_dynamic_config_degradation.py` — Postgres fora / Redis fora?
- [ ] `test_dynamic_config_concurrency.py` — duas escritas simultâneas?
- [ ] `test_dynamic_config_drift.py` — Postgres diverge de Redis, read-repair funciona?
- [ ] `test_dynamic_config_rollback.py` — reverter via histórico funciona?

---

## ✅ Documentação

### Arquivos de Referência Obrigatórios

- [ ] `.claude.md` — atualizado com decisões de LangGraph, RBAC, tool-calling?
- [ ] `docs/architecture/system-map.md` — diagrama de componentes atual?
- [ ] `docs/business/regras_negocio_oraculo.md` — regras e limites do sistema?
- [ ] `docs/technical-debt.md` — dívidas documentadas?

### Documentação do Plano A

- [ ] `docs/historico/plataforma_orientada_a_configuracao.md` — v2 atualizada?
- [ ] `docs/historico/estado_e_roteiro_planos.md` — índice único de verdade?
- [ ] `docs/historico/fases_6_11_langgraph_studio.md` — roadmap das próximas fases?
- [ ] `docs/decision_camada1_nodes.md` — decisão SIM/NÃO documentada?

---

## ✅ Pronto para Fase 6?

Se todos acima têm ✅:

### Iniciar Camada 1

1. Criar `src/graph/base_node.py` — `BaseNode` abstrato.
2. Criar `src/graph/node_registry.py` — registry com autodiscovery.
3. Refatorar `src/infrastructure/adapters/llm_provider_registry.py` → herdar de `BaseNode`.
4. Adicionar testes: `test_base_node.py`, `test_node_registry.py`.
5. Validar: todos os testes verdes, comportamento idêntico ao antes.
6. Merge → branch principal.

### Depois: Fase 6 (STT/TTS/Embeddings)

1. Criar nós: `src/graph/nodes/stt_node.py`, `tts_node.py`, `embeddings_node.py`.
2. Adicionar ao registry.
3. Criar testes de topologia + fallback.
4. Hub `/hub/graph-nodes` lista todos.

---

## Checklist de Decisão Final

Antes de começar Camada 1, responda:

- [ ] Você quer Graph Studio visual (Hub como editor de nós/arestas)?
- [ ] Você quer Fases 6–8 com alicerce robusto (Camada 1)?
- [ ] Você pode dedicar 1–2 sprints a refatoração sem bloqueador de negócio?
- [ ] Times técnico/de negócio concordam com roadmap?

**Se tudo SIM**: 🟢 Pronto para começar.

**Se algum NÃO**: 🟡 Escalate — pode precisar de decisão maior (negócio, recursos, prioridades).

---

## Help: Se faltar algo

Se algum ✅ acima não passar, procure por:
1. **Fases 1–5 incompletas**: Verificar `estado_e_roteiro_planos.md`, seção "Plano A".
2. **Plano B incompleto**: Verificar `estado_e_roteiro_planos.md`, seção "Plano B".
3. **Pré-requisitos técnicos**: Ver `.claude.md` linhas 11-16 (LangGraph, RBAC, tool-calling).
4. **Testes faltando**: Ver `docs/technical-debt.md` — há itens sobre cobertura de testes?

---

**Data de Validação**: 2026-08-28  
**Próxima revisão**: Antes de iniciar Camada 1 (3–5 dias a partir desta data)
