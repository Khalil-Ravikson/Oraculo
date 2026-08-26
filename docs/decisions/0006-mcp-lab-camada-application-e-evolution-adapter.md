# ADR 0006 — MCP lab ganha camada de Application e para de acessar EvolutionAdapter direto

- **Status:** ativo
- **Data:** 2026-08-25
- **Fonte:** Decisão 03 do plano de integração LangGraph/REST/MCP (Fase 4)

## Contexto

`mcp_lab/` é um laboratório de estudo de integração MCP (Model Context
Protocol) contra um gateway de terceiros (`gateway.pipeworx.io`) que
hospeda StackExchange/Brave Search/GitHub — BYO API keys, sem tocar
banco/infraestrutura de negócio da UEMA. Mesmo padrão de `rest_lab/`
(ADR 0005): `mcp_lab/router.py` (regex de comando, prefixo `"stack "`/
similares) → `mcp_lab/tools.py` chamava o SDK `mcp`/`httpx` **direto**.

Achado real da auditoria de 2026-08-24, distinto de `rest_lab`:
`mcp_lab/tools.py::buscar_imagem()` instanciava
`src.infrastructure.adapters.evolution_adapter.EvolutionAdapter` **direto**
pra mandar a imagem encontrada pro WhatsApp — o único ponto em todo
`rest_lab`/`mcp_lab` que tocava infraestrutura de produção sem passar por
nenhuma camada intermediária.

## Decisão

1. Introduzir `src/application/use_cases/mcp_lab_use_case.py::McpLabUseCase`
   — mesmo tratamento de `RestLabUseCase` (ADR 0005). `mcp_lab/tools.py`
   vira facade fino, `mcp_lab/router.py`/`run_test.py` não mudaram.
2. Adicionar `enviar_midia_por_url()` a
   `src/capabilities/messaging/evolution_tool.py` — a mesma capability que
   `agents/conversation/registration.py` já usa pra outra finalidade
   (`enviar_botoes_confirmacao`). `buscar_imagem()` passa a chamar essa
   capability em vez de instanciar `EvolutionAdapter` direto.

## O que NÃO mudou

`mcp_lab` continua sendo laboratório de estudo — gateado por prefixo,
interceptado em `dispatcher_langgraph.py::processar()` antes de qualquer
outra coisa. A correção do `EvolutionAdapter` é sobre **quem** chama a
infraestrutura de entrega (capability em vez de módulo de estudo direto),
não sobre mudar o que a entrega faz.

`FEATURE_MCP_PRODUCT` (criada na Fase 1): mesma situação do `FEATURE_REST_PRODUCT`
(ADR 0005) — não guarda comportamento nesta fase, fica declarada e
desligada.

## Consequências

- `mcp_lab/tools.py` deixa de ser o dono da lógica de chamada MCP/HTTP —
  qualquer mudança futura acontece em `McpLabUseCase`.
- `EvolutionAdapter` passa a ter um consumidor a menos fora de
  `src/` (produção)/`evolution_tool.py` (capability) — reduz a superfície
  de quem pode disparar entrega real de mensagem no WhatsApp.
- Primeira cobertura de teste automatizado que `mcp_lab/` já teve (14
  testes) e primeiro teste dedicado de
  `evolution_tool.py` (2 testes — antes só coberto indiretamente via
  `test_conversation_registration.py`).
