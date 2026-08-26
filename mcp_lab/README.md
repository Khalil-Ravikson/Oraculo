# `mcp_lab/` — 🧪 PESQUISA / EXPERIMENTO

**Não é parte da arquitetura de produção do Oráculo.** Continua assim
mesmo após a Fase 4 do plano de integração LangGraph/REST/MCP (Decisão
03/ADR 0006) — a mudança foi de camada (chamadas MCP/HTTP agora passam
por `src/application/use_cases/mcp_lab_use_case.py::McpLabUseCase`) e de
acesso a infraestrutura (`buscar_imagem()` não instancia mais
`EvolutionAdapter` direto, passa pela capability
`src/capabilities/messaging/evolution_tool.py::enviar_midia_por_url`), não
de propósito.

Laboratório de estudo de MCP (Model Context Protocol), mesmo espírito de
[`rest_lab/`](../rest_lab/). Prova de capacidade técnica; único ponto de
entrada no núcleo é gated por prefixo de comando (`stack `, `brave `) em
`src/application/runtime/dispatcher_langgraph.py`.

Como o cliente MCP funciona: [`ARQUITETURA.md`](ARQUITETURA.md). Detalhes de
escopo: docstring em [`__init__.py`](__init__.py). Decisões registradas:
[`docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md`](../docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md),
[`docs/decisions/0006-mcp-lab-camada-application-e-evolution-adapter.md`](../docs/decisions/0006-mcp-lab-camada-application-e-evolution-adapter.md).
Histórico de sessão: `notas.md` §10-12.
