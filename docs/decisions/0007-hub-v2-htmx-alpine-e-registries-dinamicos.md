# ADR 0007 — Hub v2: HTMX+Alpine, registries dinâmicos, GraphExecutor MVP

- **Status:** ativo
- **Data:** 2026-08-31
- **Fonte:** Redesign do Hub Admin (plano `silly-percolating-ritchie.md`,
  Sprints 0–8). Complementa `docs/architecture/arquitetura_oraculo.md` §12.

## Contexto

O portal `/hub/*` era um "painel de toggles" — tabelas e botões sobre
`admin_api.py`, com três sistemas de design competindo (Plano B já tinha
unificado o CSS, mas a densidade de informação e o vazamento de jargão
continuavam). O dono pediu: (a) tirar jargão de backend da UI, (b) permitir
adicionar provedores de LLM, canais e ferramentas **pelo painel** em vez de
editar código + deploy, (c) painéis reais do que roda por baixo (Redis Stack,
índices, saúde), (d) começar a amarrar o Graph Studio a execução real.

## Decisão

1. **HTMX + Alpine.js vendorados**, sem build step. Alternativa considerada:
   React+Vite (rejeitada — 1 dev, ~20 páginas server-rendered, não justifica
   SPA). `utilities.css` (~30 utilitários dos tokens) fica como ponte caso um
   dia se adote Tailwind. Templates seguem Jinja2 estendendo `_shell.html`.

2. **Camada de tradução central.** `_glossario.html` (macros) +
   `core/glossario.js` (`window.Glossario`). Regra dura: nenhuma página
   imprime identificador de código / tabela / migration / `.py` fora de
   `data-tech`/tooltip. Travado por `tests/unit/hub/test_no_backend_jargon.py`.

3. **Registries dinâmicos = Postgres (verdade) + espelho Redis (caminho
   quente).** Mesmo padrão já comprovado em `agentes_catalogo`/`llm_pricing`.
   Tabelas `tools_catalogo` (016), `llm_providers` (017), `canais` (018).
   - Provedor novo: só `openai_compat` (reusa `OpenAICompatibleProvider`); os
     nativos (gemini/deepseek/groq) continuam como builder de código.
   - **Chave de API nunca no banco** — a linha guarda só o *nome* da variável
     de ambiente (`api_key_env`). Alinhado ao §P de
     `plataforma_orientada_a_configuracao.md` (secrets fora de escopo até
     cliente enterprise). Vale para provedores, canais e servidores MCP.
   - Canais: só "conectar instância existente" (status/QR/webhook). O hot
     path de mensagem (`EvolutionAdapter`/`EvolutionService`) **continua
     lendo `settings.EVOLUTION_*`** — a tabela seeda com os mesmos valores;
     migrar o hot path é follow-up, seguro só com um 2º canal real.

4. **Ferramenta dinâmica executa por dado.** `dynamic_tool_executor.py`:
   tipo `http` (SSRF revalidado *na chamada*, não só no cadastro — DNS
   rebinding) e `mcp` (sessão de vida curta contra servidor cadastrado).
   `capabilities/registry.py::executar_tool` cai no executor quando o nome
   não está no registro de código. Ferramenta com lógica Python arbitrária
   continua vindo de código.

5. **GraphExecutor MVP atrás de flag desligada.** `graph_executor.py` executa
   uma topologia de `graph_topology` (ordem topológica + saída→entrada +
   respeita `graph_node_config`). `dry_run=True` (padrão) não chama
   `node.execute()`. `FEATURE_GRAPH_EXECUTOR_PILOTO` (020, default `false`) —
   **nada lê no pipeline de produção**. Não substitui o dispatcher; é a prova
   de que registry + topologia + toggle executam um trecho de ponta a ponta.

## Consequências

- Adicionar provedor/canal/ferramenta deixa de exigir deploy.
- `llm_factory._providers_validos()` virou função (era constante de módulo) —
  provedor cadastrado é selecionável sem restart.
- Ação destrutiva de infra no Hub **só se cirúrgica** (namespace específico).
  Um botão "Limpar cache" que fazia `FLUSHDB` cru foi removido após apagar,
  em teste, os índices RediSearch + chunks de RAG (que não se reconstroem
  sozinhos). Lição registrada.
- Dívidas abertas: `RedisVLVectorAdapter.buscar_hibrido` emite `FT.HYBRID`
  (não suportado nesta versão do Redis Stack — hot path usa o caminho sync);
  `llm_circuit_breaker.status()` ainda hardcoded nos 3 nativos.
