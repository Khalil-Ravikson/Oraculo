# `mcp_lab/` — 🧪 PESQUISA / EXPERIMENTO

**Não é parte da arquitetura de produção do Oráculo.**

Laboratório de estudo de MCP (Model Context Protocol) — branch/worktree
`research/rest-mcp-estudos`, mesmo espírito de [`rest_lab/`](../rest_lab/).
Prova de capacidade técnica; único ponto de entrada no núcleo é gated por
prefixo de comando (`stack `, `brave `) em
`src/application/runtime/dispatcher_langgraph.py`.

Como o cliente MCP funciona: [`ARQUITETURA.md`](ARQUITETURA.md). Detalhes de
escopo: docstring em [`__init__.py`](__init__.py). Decisão registrada:
[`docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md`](../docs/decisions/0004-multi-provider-llm-e-roteamento-nos-labs.md).
Histórico de sessão: `notas.md` §10-12.
