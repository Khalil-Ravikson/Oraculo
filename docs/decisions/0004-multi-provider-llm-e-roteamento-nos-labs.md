# ADR 0004 — Multi-provider LLM via `ILLMProvider`, e roteamento por regex (não LLM) nos laboratórios de pesquisa

- **Status:** ativo
- **Data:** multi-provider em 2026-08-15; labs a partir de 2026-07-31
- **Fonte:** extraído de `.claude.md` (versão anterior a 2026-08-25), `analise_custo_real_llm.md`, `notas.md` §10-13

## Parte 1 — Multi-provider LLM (Gemini/DeepSeek/Groq)

### Contexto

A abstração `ILLMProvider` (`domain/ports/llm_Provider.py`) já existia bem
desenhada, mas quase nada a usava — 9 arquivos chamavam `genai.Client`
direto, sem telemetria real de custo (`metricas_llm` nunca era escrito;
Prometheus só era chamado por código já morto).

### Decisão

Migrar todas as chamadas de geração de texto para passar por
`get_llm_provider()` (`infrastructure/adapters/llm_factory.py`), que sempre
devolve um `MonitoredLLMProvider` — grava telemetria real (Postgres
`metricas_llm` + Prometheus) automaticamente. Novo adapter genérico
`openai_compatible_provider.py` cobre DeepSeek e Groq com uma única classe
(API compatível OpenAI). Provider trocável em runtime via Redis
(`admin:llm_provider`, editável em `/hub/llm-custo`), com override opcional
por agente.

### Consequências

- Único chamador de `genai.Client` direto que resta, deliberadamente fora do
  contrato: `gemini_stt_provider.py` (é transcrição de áudio, não geração de
  texto).
- Regra de código novo: nunca chamar `genai.Client` direto — usar
  `get_llm_provider()`.

## Parte 2 — Roteamento por regex, não LLM, nos laboratórios de pesquisa

### Contexto

`rest_lab/` e `mcp_lab/` (branch `research/rest-mcp-estudos`) são provas de
capacidade técnica, não features de produto — estudo de API REST e de MCP
(Model Context Protocol) respectivamente.

### Decisão

Roteamento determinístico por regex com prefixo de comando (`rest `,
`stack `, `brave `) — não classificação por LLM. Ver `rest_lab/router.py` /
`mcp_lab/router.py::tentar_rotear()` (devolve `None` se não reconhecer,
cai no fluxo normal).

### Achado relevante para decisões futuras de tool-calling

O Oráculo hoje **não tem nenhum function-calling nativo de LLM funcionando**
em produção — o padrão real é saída estruturada forçada (`response_schema`
Pydantic via `google.genai`). Se algum dia for decidido introduzir
tool-calling de verdade, o caminho natural é `google.genai`, não LangChain
`bind_tools`.

### Consequências

- `mcp_lab.router.tentar_rotear()` e `rest_lab.router.tentar_rotear()` são
  chamados como fast-paths em `dispatcher_langgraph.py::processar()`, antes
  do Planner — único ponto do núcleo tocado por esses laboratórios.
- `docker-compose.yml` precisa manter `rest_lab/`/`mcp_lab/` montados como
  volume nos mesmos serviços que montam `./src`.
- `httpx` e `mcp` foram promovidos para `requirements.txt` — vão para a
  imagem Docker de `main` mesmo sem uso lá (mesma observação do ADR 0001
  sobre dependências experimentais vazando para produção).
