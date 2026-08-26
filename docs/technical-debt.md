# Registro de Dívida Técnica

> Criado em 2026-08-25 durante a organização documental. Este documento
> **registra** problemas — não os resolve. Cada item veio da mega auditoria
> de 2026-08-24 e/ou foi confirmado durante a organização de 2026-08-25.
> Prioridade é uma opinião de ponto de partida, não uma ordem imposta.

| ID | Resumo | Prioridade sugerida |
|---|---|---|
| [TD-001](#td-001--dois-orquestradores-de-mensagem) | Dois orquestradores de mensagem (`dispatcher.py` + `dispatcher_langgraph.py`) | Alta |
| [TD-002](#td-002--fast-path-paralelo-ao-planner) | Fast-Path em `dispatcher.py` contorna o Planner | Alta |
| [TD-003](#td-003--migração-servicescapabilities-incompleta) | Migração `services/` → `capabilities/` incompleta | Média |
| [TD-004](#td-004--pyprojecttoml-desatualizado) | `pyproject.TOML` desatualizado e não lido por nada | Baixa |
| [TD-005](#td-005--dependências-experimentais-na-imagem-de-produção) | Dependências experimentais (`langgraph`, `mcp`, `kokoro`) na imagem de `main` | Média |
| [TD-006](#td-006--cobertura-de-testes-insuficiente) | Cobertura de testes zero em `memory/`, `rag/`, `services/` | Média |
| [TD-007](#td-007--import-potencialmente-quebrado-em-query_transformpy) | Import quebrado em `rag/query_transform.py` | Média |
| [TD-008](#td-008--redis-diferente-entre-ci-e-produção) | Redis diferente entre CI e produção | Baixa |
| [TD-009](#td-009--risco-de-oom-no-worker_media) | Risco de OOM não testado em `worker_media` | Alta |
| [TD-010](#td-010--gemini_model-preview-não-fixado) | `GEMINI_MODEL` apontando para versão *preview* | Alta |
| [TD-011](#td-011--migration-004_recria_tabela_pessoas-sem-explicação-no-histórico) | Migração `004_recria_tabela_pessoas` sem explicação no histórico Alembic | Média |
| [TD-012](#td-012--testwiki_scraperpy-órfão) | `tests/test_wiki_scraper.py` órfão (import quebrado) | Baixa |
| [TD-013](#td-013--gatekeeper-ignore-reescrito-incondicionalmente-para-llm) | Gatekeeper: toda decisão `IGNORE` é reescrita pra `LLM` — filtros de segurança inertes | Média |
| [TD-014](#td-014--4-arquivos-de-teste-e2e-órfãos-import-quebrado) | 4 arquivos em `tests/e2e/` órfãos (import quebrado) | Baixa |

---

## TD-001 — Dois orquestradores de mensagem

**Problema:** `src/application/runtime/dispatcher.py` (legado) e
`src/application/runtime/dispatcher_langgraph.py` (produção) coexistem,
cada um com sua própria lógica de fast-path (ex.: interceptação de STT
duplicada entre os dois).

**Estado atual:** `dispatcher_langgraph.py` é o caminho real de produção,
chamado por `process_message_task.py` (worker Celery). `dispatcher.py`
continua vivo, chamado por `api/chain_sse.py`, `eval_api.py` e `hub.py`
(debug/SSE/eval) — não é código morto, é um segundo caminho ativo.

**Impacto:** qualquer mudança de comportamento de roteamento precisa ser
verificada nos dois arquivos. Já causou um bug real de produção (nota de
voz virando embedding vazio — corrigido num lugar, só descoberto no outro
depois, ver `notas.md` §11 "Bug real de produção pego no primeiro teste real
com voz").

**Evidência:** `notas.md` §11; `.claude.md` (regras); auditoria de
2026-08-24.

**Escopo desta execução:** documentado, **não resolvido**.

**Recomendação futura:** ⚠️ **REQUER DECISÃO ARQUITETURAL FUTURA** — decidir
se `dispatcher.py` deve ser aposentado (migrando SSE/eval/debug para
`dispatcher_langgraph.py`) ou se os dois caminhos são intencionalmente
permanentes por terem consumidores com necessidades diferentes. Não é uma
decisão técnica trivial — depende do destino da avaliação do LangGraph (ver
`docs/decisions/0001-langgraph-nao-aprovado-para-main.md`).

---

## TD-002 — Fast-Path paralelo ao Planner

**Problema:** `dispatcher.py` (~linha 319-388) despacha tasks Celery
diretamente para `MEDIA_DOWNLOAD`/`SIGAA`/`TICKET_ABERTURA`/`CRUD`,
contornando o Planner — o próprio código comenta que é "um caminho paralelo
ao Planner".

**Impacto:** roteamento de mensagem real tem três lugares a checar
(`router/supervisor.py`, o Fast-Path deste arquivo, e o roteamento normal de
`dispatcher_langgraph.py`), sem uma fonte única de verdade.

**Evidência:** auditoria de 2026-08-24 (seção de duplicações).

**Escopo desta execução:** documentado, **não resolvido**. Relacionado a
TD-001.

---

## TD-003 — Migração `services/`→`capabilities/` incompleta

**Problema:** `src/services/` ainda tem 4 arquivos (incl.
`registration_service.py`, 530 linhas) que deveriam ter migrado para
`src/capabilities/` na refatoração documentada em
`docs/historico/PLANO_REFATORACAO_SUPERVISOR.md` §0.1b — que já sinalizava
isso como fora de escopo daquele roadmap.

**Impacto:** duas estruturas paralelas para o mesmo tipo de responsabilidade
(`services/` vs `capabilities/`), confuso para quem está aprendendo o
projeto.

**Evidência:** `docs/historico/PLANO_REFATORACAO_SUPERVISOR.md` §0.1b.

**Escopo desta execução:** documentado, **não resolvido** (mover código
ativo é refatoração, fora do escopo desta organização).

---

## TD-004 — `pyproject.TOML` desatualizado

**Problema:** extensão não-padrão (`.TOML` maiúsculo), dependências
divergentes de `requirements.txt` (ex.: `google-genai==1.65.0` fixo vs.
`>=0.5.0` real), auto-marcado `#desatualizado:#` pelo próprio time. Não é
lido por build, Docker nem CI — só `requirements.txt` é a fonte real.

**Impacto:** confunde quem tenta reproduzir o ambiente a partir dele.

**Evidência:** auditoria de 2026-08-24 (seção de configuração).

**Escopo desta execução:** **não alterado** — decisão sobre unificar/remover
é gerenciamento de dependências, fora do escopo desta organização.

---

## TD-005 — Dependências experimentais na imagem de produção

**Problema:** `langgraph`, `langgraph-checkpoint-redis`, `mcp`, `kokoro`,
`lameenc` estão em `requirements.txt` (comentados como "só para
experimentos"), mas o `Dockerfile` instala `requirements.txt` por inteiro —
essas dependências vão para a imagem de `main` mesmo sem uso ativo lá.
Kokoro é baixado em build-time por um `RUN` que a própria `.claude.md`
(versão anterior) admitia nunca ter sido testado em `docker build` real.

**Impacto:** risco de quebra de build se esta branch for mesclada sem
validar; imagem de produção maior que o necessário.

**Evidência:** auditoria de 2026-08-24 (seção de configuração).

**Escopo desta execução:** **não alterado** — mexer em Docker/dependências
está fora do escopo desta organização.

---

## TD-006 — Cobertura de testes insuficiente

**Problema:** `memory/` (16 arquivos), `rag/` (9 arquivos) e `services/`
(4 arquivos) não têm nenhuma pasta de teste unitário dedicada.

**Impacto:** mudanças nesses subsistemas não têm rede de segurança
automatizada.

**Evidência:** auditoria de 2026-08-24 (seção de código).

**Escopo desta execução:** **não alterado** — aumentar cobertura é trabalho
de engenharia, fora do escopo desta organização.

---

## TD-007 — Import potencialmente quebrado em `query_transform.py`

**Problema:** `src/rag/query_transform.py:14` faz
`from src.memory.long_term_memory import Fato, fatos_como_string` — esse
módulo só existe como `src/memory/long_term_memory.bak` (não um `.py`
importável). Confirmado via `python3 -c "import src.rag.query_transform"`:
`ModuleNotFoundError`.

**Estado atual:** o próprio `src/agents/academic_knowledge/query_transform.py`
(o módulo realmente usado em produção) documenta em seu docstring que
`rag/query_transform.py` é "um pipeline de RAG mais antigo... que não está
no caminho quente de produção — só é exercitado por
`application/use_cases/retrieve_context_use_case.py` e por um script manual
`tests/e2e/test_novo_oraculo.py`". Ou seja: quebrado, mas não em produção.

**Decisão explícita desta sessão:** `long_term_memory.bak` **não foi
removido** justamente por causa desta referência — mesmo sabendo que a
quebra do import independe da presença do `.bak` (extensão `.bak` nunca foi
importável), preservamos o arquivo para não obscurecer o rastro de onde
`Fato`/`fatos_como_string` viviam. O equivalente moderno dessas duas coisas
hoje é `src/memory/ports/long_term_port.py` (classe `Fato`) — um possível
caminho de correção futura, não aplicado aqui.

**Evidência:** teste direto de import, 2026-08-25; docstring de
`agents/academic_knowledge/query_transform.py`.

**Escopo desta execução:** documentado, **não corrigido** (corrigir é
mudança de código/lógica).

---

## TD-008 — Redis diferente entre CI e produção

**Problema:** `docker-compose.yml` usa `redis/redis-stack:latest` (com
RedisJSON/RediSearch); `.github/workflows/tests.yml` sobe `redis:7-alpine`
(sem esses módulos).

**Impacto:** um teste que dependesse de comandos `FT.*`/`JSON.*` reais
poderia passar local e falhar/pular no CI, ou vice-versa.

**Evidência:** auditoria de 2026-08-24 (seção de configuração).

**Escopo desta execução:** **não alterado** — mexer em CI/Docker está fora
do escopo desta organização.

---

## TD-009 — Risco de OOM no `worker_media`

**Problema:** `worker_media` roda com `mem_limit: 768m` — o mesmo limite
que já causou OOM-kill no worker `default` (768m) antes da correção que
moveu STT/TTS (Kokoro/torch) para lá. A própria `.claude.md` (versão
anterior) chamava isso de "risco residual não testado".

**Impacto:** possível OOM em produção sob carga real de STT/TTS.

**Evidência:** `notas.md` §12; auditoria de 2026-08-24.

**Escopo desta execução:** **não alterado** — mexer em `mem_limit`/Docker
está fora do escopo desta organização.

---

## TD-010 — `GEMINI_MODEL` preview não fixado

**Problema:** `.env` aponta para `GEMINI_MODEL=gemini-3.1-flash-lite-preview`
— sinalizado em `notas.md` §9.10 (2026-07-31) como propenso a erro 404,
nunca corrigido.

**Impacto:** risco operacional ativo — não histórico.

**Evidência:** `notas.md` §9.10; `.env` (não versionado, verificado
localmente).

**Escopo desta execução:** **não alterado.** ⚠️ **AÇÃO FUTURA —
INFRA/PRODUÇÃO** (configuração operacional, não arquivo versionado).

---

## TD-011 — Migration `004_recria_tabela_pessoas` sem explicação no histórico

**Problema:** o nome do arquivo ("recria" = recria) sugere que a tabela
`pessoas` foi derrubada e recriada em algum momento anterior ao histórico
do Alembic (a cadeia de revisões é linear e contígua — não há uma "003b"
órfã que a tenha criado originalmente), o que aponta para uma intervenção
manual (SQL direto) antes desta migração existir.

**Impacto:** possível perda de dados histórica não documentada — não
confirmável só pela leitura do código.

**Evidência:** `migrations/versions/004_recria_tabela_pessoas.py`; auditoria
de 2026-08-24.

**Escopo desta execução:** **não investigado além da leitura do código** —
⚠️ **NECESSITA VALIDAÇÃO HISTÓRICA** com quem operou o banco antes dessa
migração existir.

---

## TD-012 — `test_wiki_scraper.py` órfão

**Problema:** `tests/test_wiki_scraper.py` importa
`src.domain.tools.tool_wiki_ctic`, módulo que não existe mais (removido na
migração para `capabilities/`). Confirmado durante a organização de
2026-08-25 ao revisar `docs/historico/notas_regras_negocio_chunkviz.md`.

**Impacto:** o arquivo quebraria com `ImportError` se executado; não está
em `tests/unit/` então não afeta a suíte principal, mas é lixo de teste que
confundiria qualquer um rodando `pytest` sem escopo (`pytest` na raiz, sem
argumento, tentaria coletá-lo).

**Evidência:** `grep tool_wiki_ctic tests/test_wiki_scraper.py`.

**Escopo desta execução:** documentado, **não removido** (é um arquivo de
teste — removê-lo sem decisão explícita do usuário não é seguro o
suficiente para esta rodada de organização).

---

## TD-013 — Gatekeeper `IGNORE` reescrito incondicionalmente para `LLM`

**Problema:** `src/application/tasks/process_message_task.py:353-354` —
toda decisão `DispatchTarget.IGNORE` do Gatekeeper (`gatekeeper.py::
MessageRouter.route()`) é reescrita incondicionalmente para `LLM` antes de
qualquer outra coisa acontecer:

```python
if decision.target == DispatchTarget.IGNORE:
    decision.target = DispatchTarget.LLM
```

**Impacto:** os filtros de segurança do Gatekeeper (grupo estranho, texto
vazio, não-admin em comando admin, etc.) não bloqueiam nada hoje — só mudam
o motivo registrado no log (`decision.reason`), nunca o destino real do
processamento. Pré-existente em `main`, não introduzido pelo plano de
integração LangGraph/REST/MCP — descoberto durante a auditoria de
2026-08-25 ao mapear os consumidores do Gatekeeper.

**Evidência:** `process_message_task.py:353-354`; plano de integração
LangGraph/REST/MCP, achado de arquitetura de 2026-08-25.

**Escopo desta execução:** documentado, **não corrigido** — mexer no
comportamento do Gatekeeper é decisão de segurança/produto que precisa de
avaliação própria (por que o override foi introduzido, o que quebraria se
removido), fora do escopo aprovado do plano de integração (Decisões 00-06).

---

## TD-014 — 4 arquivos de teste e2e órfãos (import quebrado)

**Problema:** `tests/e2e/test_llm.py`, `tests/e2e/test_llm_rag.py`
importam `src.application.graph.nodes` — módulo que não existe (não
confundir com `langgraph_experiment/`, que é outra coisa).
`tests/e2e/test_redis_rag_fluxo.py` e `tests/e2e/test_novo_oraculo.py`
importam `RedisVectorAdapter` de `src.infrastructure.adapters.redis_vector_adapter`
— a classe real hoje se chama `RedisVLVectorAdapter`. `pytest tests/e2e
--collect-only` falha nos 4 com `ModuleNotFoundError`/`ImportError`.

**Estado atual:** pré-existente, confirmado via `git log` (último commit
que tocou esses arquivos é `3e1bb5c`, bem anterior a qualquer trabalho do
plano de integração LangGraph/REST/MCP). Não é executado pelo CI (que só
roda `tests/unit` + o eval do wiki CTIC) nem por nenhum passo desta
integração — só apareceu ao tentar coletar `tests/e2e/` inteiro como parte
da checagem da Fase 7 do plano.

**Impacto:** baixo — arquivos órfãos que quebrariam se alguém rodasse
`pytest tests/e2e` sem saber disso; mesma classe de achado do TD-012.

**Evidência:** `pytest tests/e2e --collect-only -q`, 2026-08-25.

**Escopo desta execução:** documentado, **não corrigido** (atualizar/
remover é decisão sobre arquivos de teste fora do escopo das Decisões
00-06, mesmo raciocínio do TD-012).
