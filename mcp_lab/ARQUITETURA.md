# Como o `mcp_lab/` se conecta a um servidor MCP

Documento de estudo — explica a montagem feita para o piloto StackExchange (`mcp_lab/`), pensado pra servir de referência quando você for plugar o próximo servidor MCP (GitHub, Brave, yt-dlp).

## 1. O que é MCP, sem rodeio

MCP (Model Context Protocol) é um protocolo JSON-RPC. De um lado tem um **servidor MCP** — um processo (local, via `stdio`) ou um serviço HTTP remoto — que expõe uma lista de **tools** (nome, descrição, schema de parâmetros). Do outro lado tem um **client MCP** — código seu — que fala esse protocolo: conecta, pede `initialize()`, pode listar tools (`list_tools()`) e chamar uma (`call_tool(nome, args)`). O servidor devolve um resultado padronizado (`CallToolResult`), com o conteúdo de verdade dentro de `.content` (geralmente texto, às vezes JSON serializado como texto).

A parte "IA" nisso é opcional: o protocolo não obriga um LLM decidir qual tool chamar. No `mcp_lab/`, quem decide é regex (`router.py`) — o LLM nem participa. Isso é uma escolha deste laboratório, não uma limitação do protocolo (ver seção 4).

## 2. As duas peças do lado do client

### a) Transporte — `mcp_lab/clients.py`

```python
async with streamable_http_client(URL) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        yield session
```

- `streamable_http_client` é o transporte HTTP do SDK `mcp` (existe também `stdio_client` para servidores locais que rodam como subprocesso, e `sse_client` para Server-Sent Events — variam por versão do SDK, checar sempre).
- `ClientSession` é a camada de protocolo por cima do transporte: `initialize()` faz o handshake (troca de capacidades cliente↔servidor).
- **Decisão deste laboratório**: sessão de vida curta — abre, usa, fecha a cada chamada (`async with` inteiro dentro da função da tool). Isso é uma escolha de custo/simplicidade, não exigência do protocolo — dá pra manter uma sessão viva entre chamadas se o volume justificar (ver seção 5).

### b) Chamada da tool — `mcp_lab/tools.py`

```python
resultado = await session.call_tool("search_questions", {"query": query, "site": site})
```

- `resultado.content` é uma lista de blocos (quase sempre `TextContent`, com `.text`).
- `resultado.is_error` indica se o servidor sinalizou erro (server-side), diferente de exceção de rede/protocolo (que estoura como `Exception` no `try/except` do client).
- Cada servidor decide o formato do que vai dentro do texto — no caso da StackExchange, é JSON serializado (por isso `json.loads()` em `_extrair_dados()`). Outro servidor pode devolver Markdown puro, ou texto livre — **sempre inspecionar o retorno real antes de assumir o formato**, a documentação do servidor nem sempre bate com o comportamento (foi o caso aqui: a doc do pipeworx não citava nomes de função nem formato exato, o SDK instalado também divergia de exemplos genéricos — ver `.claude.md`).

## 3. O roteamento — por que regex e não o LLM decidindo

`router.py` intercepta a mensagem do WhatsApp **antes** de qualquer chamada de LLM, via prefixo fixo (`stack `). Vantagens desta escolha, nesta fase de estudo:
- Previsível: sempre a mesma tool pro mesmo padrão de comando.
- Sem custo de tokens/latência de um LLM decidindo.
- Zero risco de "alucinar" uma tool ou parâmetro errado.

Desvantagem: não escala — cada tool nova precisa de uma regex nova, e o usuário precisa saber o comando exato. Isso é aceitável para 2-5 tools de um laboratório; não é o desenho que um produto real usaria para um catálogo grande de tools (ver seção 4).

## 4. Onde isso se encaixa numa arquitetura de agente "de verdade"

Ver a resposta completa na conversa (seção "profissional") — resumo: function-calling real via LLM (o modelo recebe a lista de tools + schemas, decide sozinho qual chamar e com quais argumentos) é o padrão de mercado quando o catálogo de tools cresce além de um punhado. O Oráculo hoje não tem isso rodando em produção (`notas.md` §10.1) — o caminho natural seria `google.genai` com `tools=[...]` (mesmo provider já usado pra `response_schema`), não LangChain `bind_tools` (comprovadamente não adotado, código morto em `gmail_tool.py`).

## 5. Coisas para reavaliar antes de qualquer coisa virar produto (não decidir agora)

- **Sessão de vida curta vs. persistente**: cada chamada hoje paga o custo de handshake MCP de novo. Se o volume no WhatsApp crescer, uma sessão persistente por worker economiza latência — mas reintroduz a mesma classe de problema de "recurso async atravessando `asyncio.run()`" já resolvida a duras penas pro LangGraph (`AsyncRedisSaver`, ver `.claude.md`). Não trocar sem motivo real.
- **Autenticação**: StackExchange (piloto) não pediu chave. GitHub MCP e Brave MCP exigem token/API key — precisa de um lugar seguro pra guardar isso (`settings`/`.env`, nunca hardcoded).
- **Versão do SDK `mcp`**: já mudou nome de função e assinatura entre versões (`streamablehttp_client` → `streamable_http_client`, `isError` → `is_error`) — testar de novo sempre que atualizar a versão pinada.
