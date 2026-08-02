"""
mcp_lab/
==========
Laboratório de estudo de MCP (Model Context Protocol) — branch/worktree
`research/rest-mcp-estudos`, Oraculo-rest-mcp. Objetivo: aprender a
conectar um cliente MCP a um servidor real (não mockado) e consumir tools
descobertas via protocolo, como prova de capacidade técnica para o Oráculo
(CETIC/UEMA) — não é feature de produto e não tem integração com `src/` do
núcleo, além do único ponto de entrada explícito documentado em
`dispatcher_langgraph.py`. Segue o mesmo espírito de `rest_lab/` (ver
`rest_lab/__init__.py`) e fecha a pendência de "estudo de MCP" registrada em
`notas.md` §10.7.

Diferença chave para `rest_lab/`: lá as tools chamam `httpx` direto contra
endpoints REST específicos; aqui o SDK oficial `mcp` cuida do transporte —
o cliente abre uma sessão MCP, descobre/chama tools de forma uniforme
(`session.call_tool(nome, args)`), sem wrapper HTTP escrito à mão.

Servidor piloto: StackExchange MCP, hospedado pela pipeworx.io
(https://gateway.pipeworx.io/stackexchange/mcp), transporte HTTP
(`streamable-http`). Tools consumidas nesta rodada: `search_questions` e
`get_answers` (das 5 expostas pelo servidor — as outras 3 seguem o mesmo
padrão se algum dia forem adicionadas).

Escopo desta rodada: roteamento determinístico (regex -> tool), sem LLM
decidindo a chamada — mesma decisão do `rest_lab/router.py` e mesmo motivo
(`notas.md` §10.1: Oráculo não tem function-calling LLM funcionando em
produção hoje). Sessão MCP de vida curta por chamada (abre, chama 1 tool,
fecha) — menor consumo de RAM, evita reintroduzir a classe de bug de
event-loop-persistente-por-worker já documentada pro LangGraph em
`.claude.md` (`AsyncRedisSaver` NUNCA pode atravessar `asyncio.run()`).
Teste só via CLI local (`run_test.py`) primeiro, sem WhatsApp/Celery/
dispatcher.
"""
