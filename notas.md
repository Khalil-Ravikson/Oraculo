# notas.md

> Rascunho de anotações rápidas sobre problemas encontrados/corrigidos durante
> a rodada de testes de ponta-a-ponta de tickets/CRUD/cadastro via WhatsApp
> (2026-07-21). Não é documentação de arquitetura definitiva — ver
> `arquitetura_oraculo.md` e `notas_regras_negocio_chunkviz.md` para isso.

---

## 1. "Três cérebros" de roteamento brigando entre si (corrigido parcialmente)

### O bug observado

Mensagens em linguagem natural como "quero abrir um ticket" ou "quero
atualizar meu setor" não chegavam nos novos fluxos `TICKET_ABERTURA`/`CRUD`
— caíam no Planner genérico, que falhava com:

```
❌ [DISPATCHER] Falha ao localizar worker crud_confirm no registry
```

### Causa raiz (cadeia completa)

Para texto livre (sem `!`/`@`/`$`), `application/runtime/dispatcher.py`
consulta **dois classificadores independentes**:

1. `router/llm_fallback.py::orchestrate()` — decide uma ação de alto nível
   (`reply_direct`, `call_rag`, `call_sigaa`, `check_status`, `call_media`).
   Não conhecia `TICKET_ABERTURA`/`CRUD` (corrigido — ver abaixo).
2. `router/supervisor.py::rotear()` — 5 camadas próprias (regex → heurística
   → regex seeded → KNN → Flash), que sim tinha o regex/Flash pra
   `TICKET_ABERTURA`.

O `dispatcher.py` roda o Orquestrador **primeiro** e depois **sempre**
sobrescreve `decision.rota` (o que o Supervisor decidiu) pelo resultado do
Orquestrador, para qualquer mensagem que não seja comando — mesmo quando o
Supervisor tinha acertado.

**Bug adicional, mais sutil:** o override trocava só `decision.rota`, e
**deixava `decision.dag_hint` com o valor antigo** (calculado pra rota
original do Supervisor). O Planner (Gemini Pro, `agents/academic_knowledge/planning.py`)
recebia `"Rota detectada: GERAL"` mas `"Dica do router: {'steps': ['ticket_abertura']}"`
— informação contraditória. Diante disso, o modelo "resolvia" sozinho
escolhendo o worker mais parecido da sua própria whitelist (`VALID_WORKERS`),
que incluía `crud_confirm` — **um worker que nunca foi implementado de
verdade** (achado já documentado antes de mim em `agents/tickets/service.py`
e `capabilities/registry.py`, mas nunca removido da whitelist).

### O que foi corrigido

- `router/llm_fallback.py`: `orchestrate()` agora conhece `call_ticket` e
  `call_crud_update` como ações válidas, com descrição no prompt distinguindo
  as duas (ticket = problema/pedido novo; CRUD = corrigir dado já existente).
- `application/runtime/dispatcher.py`: mapeia essas duas ações pras rotas
  `TICKET_ABERTURA`/`CRUD`, **e recalcula `decision.dag_hint` junto com
  `decision.rota`** no override — rota e hint nunca mais ficam dessincronizados.
- `router/contracts.py` (`VALID_WORKERS`) e `agents/academic_knowledge/planning.py`:
  removido `crud_confirm` da whitelist e do prompt do Planner — não existe,
  nunca existiu implementado. Fallback de segurança: se `CRUD`/`TICKET_ABERTURA`
  chegar no Planner por algum caminho que não seja o `dispatcher.py` (não
  deveria acontecer — ele intercepta as duas rotas antes do Planner), agora
  cai num plano `greeting` inofensivo em vez de referenciar um worker fantasma.

### Variante do bug encontrada depois (2026-07-21, fechando a rodada) — ainda não corrigida

Ticket funcionou via texto livre ("Iniciar cadastro"), CRUD não (mensagem
"Crud"). Causa: quando `orchestrate()` **falha** (exceção/JSON inválido —
acontece com frequência alta, ver logs cheios de `❌ [ORCHESTRATOR] JSON
Inválido: 'Here is'`), o except handler retorna um fallback HARDCODED
(`action="call_rag", route_hint="GERAL"`). Esse fallback é tratado pelo
`dispatcher.py` como se fosse uma decisão real do Orquestrador — e por isso
**sempre sobrescreve** a classificação do Supervisor, mesmo quando o
Supervisor acertou (`rota=CRUD conf=1.00` no log, apagado por baixo do
"GERAL" de emergência). Resultado: cai no Planner genérico, que também
falha, e a mensagem nunca chega no `crud_tool.py`/`ticket_flow.py`.

Distinção que falta no código: "Orquestrador decidiu X" (deve poder
sobrescrever o Supervisor) vs. "Orquestrador falhou e isto é só um valor de
emergência" (NÃO deveria sobrescrever nada — deveria deixar o Supervisor
decidir sozinho). Hoje os dois casos são indistinguíveis pro `dispatcher.py`
porque o fallback usa o mesmo formato de uma decisão válida. Fix sugerido
(não aplicado ainda, propositalmente — é candidato natural pro plano de
unificação dos classificadores da próxima conversa, não outro remendo
pontual): os except handlers de `orchestrate()` deveriam sinalizar falha de
forma distinguível (ex: `action="orchestrator_failed"`), e o `dispatcher.py`
tratar esse caso como `decision_rota = None` (não sobrescreve nada) em vez
de forçar "GERAL".

### O que NÃO foi feito (decisão consciente, não é dívida esquecida)

Não fundi os dois classificadores (Orquestrador + Supervisor) num só. Isso
seria uma limpeza arquitetural válida — hoje são 2-3 chamadas Gemini por
mensagem decidindo intenção sem se coordenar, e esse tipo de bug de
precedência pode se repetir de outras formas — mas é uma decisão de
arquitetura maior, fora do escopo de "consertar o bug desta rodada". Fica
registrado aqui como candidato a discussão futura, não como algo pra
resolver sem avisar.

---

## 2. Bug do RegistrationFunnel: botões iam pro JID errado (corrigido)

### O bug observado

Depois de "Nome" + "Curso" preenchidos, o funil de cadastro tentava mandar os
botões de confirmação e sempre falhava:

```
❌ Evolution sendButtons → HTTP 400 | Resp: {"jid":"175174737518829@s.whatsapp.net","exists":false}
```

Como a exceção era engolida silenciosamente **dentro** de
`capabilities/messaging/evolution_tool.py::enviar_botoes_confirmacao()` (log
de erro lá, sem re-lançar), o `try/except` do `RegistrationFunnel` nunca
disparava o fallback de texto — o usuário não recebia confirmação
NENHUMA (nem botão, nem texto), achava que o cadastro não tinha funcionado, e
reenviava nome/curso de novo. Isso reiniciava o funil (3x no log de teste).

### Causa raiz

`agents/conversation/registration.py` chamava
`enviar_botoes_confirmacao(number=sender, ...)` — `sender` é o JID do
**remetente individual dentro do grupo**, não o JID do grupo. Em grupo,
toda entrega tem que ser endereçada ao JID do **grupo** (`chat_id`/`remote_jid`),
igual o resto do funil já faz (`gateway.enviar_mensagem(chat_id, reply)`).

Piorou porque o WhatsApp mudou o addressing de contatos pra `@lid`
(identificador de privacidade) em vez do número de telefone puro — o
`175174737518829` no erro é o LID, não o telefone real da pessoa
(`559887680098`, visível como `participantAlt` no webhook). Tentar montar um
JID `@s.whatsapp.net` a partir do LID nunca vai existir de verdade.

### O que foi corrigido

- `RegistrationFunnel.process()` ganhou parâmetro `chat_id` — usado no envio
  de botões (`number=chat_id or sender`) em vez de `sender`.
- `process_message_task.py` e `ConversationAgent.execute()` (call sites)
  atualizados para passar `chat_id`.

### Pendência relacionada (não mexida ainda)

O fallback interno de `enviar_botoes_confirmacao()` continua engolindo a
exceção sem propagar — se o envio de botão falhar de novo por outro motivo no
futuro, o usuário vai ficar sem NENHUMA mensagem de confirmação de novo,
silenciosamente. Vale revisar `capabilities/messaging/evolution_tool.py`
depois pra re-lançar (ou pelo menos retornar um booleano de sucesso) em vez
de só logar.

---

## 3. Flags de teste ativas nesta rodada (lembrar de desligar depois)

- `DEV_TEST_NO_DB_WRITE=true` — cadastro/ticket/CRUD gravam JSON em
  `dados/tmp/*_dev/` em vez de tocar `pessoas` de verdade.
- `DEV_TEST_SKIP_REGISTRATION=true` — libera qualquer remetente a pular o
  funil de cadastro (senão, com a flag acima ligada, ninguém "vira
  registrado" de verdade e o gatekeeper força `REGISTER_MODE` pra sempre,
  loop sem saída).

Ambas em `src/infrastructure/settings.py`, opt-in via `.env`, default
`False`. **Religar antes de ir pra produção.**

---

## 4. Ainda não investigado: RAG retornando 0 chunks

Buscas em `CONTATOS`/`EDITAL` retornaram `0 chunks` mesmo com conteúdo
existente no Redis (ex: mock de contato do PROG). Log mostra "RAG busca
vazia. Acionando Step-Back Fallback" seguido de 0 chunks de novo. Suspeita
inicial (não confirmada): descompasso entre o `doc_type` pedido pela busca
(`contatos`, `edital`) e a tag real do chunk indexado (o nome do mock sugere
`doc_type=geral`). Não investigado a fundo ainda — problema separado do
roteamento, não mexido nesta rodada.

**Confirmado 2026-07-21 18:10:** quando a rota cai como `geral` (ex:
Orquestrador falhou e caiu no fallback), a busca acha os 5 chunks sem
problema (`doc=geral` bate com a tag real do chunk). Quando a rota vira
`contatos` explicitamente, dá 0 chunks sempre — bate com a suspeita: o filtro
de `doc_type` na busca híbrida é estrito e a tag real de TODOS os chunks
mock é `geral`, não `contatos`/`edital`/etc. Ou re-tagueia os chunks mock
com o `doc_type` certo, ou a busca por rota devia cair pra "geral" como
superset quando o filtro específico não retorna nada.

**CAUSA RAIZ EXATA (`redis_client.py::salvar_chunk` + `worker_rag_search.py`):**
`salvar_chunk()` grava um campo `tipo_doc` (o campo REALMENTE usado no
filtro RediSearch, um TAG separado de `doc_type`) que, se não vier explícito
na ingestão, cai no default `doc_type.capitalize()`. Esse mock foi ingerido
com `doc_type="geral"` sem `tipo_doc` explícito → gravou `tipo_doc="Geral"`
em TODO chunk, incluindo os de contato. O worker de busca
(`worker_rag_search.py:59-61`) filtra por `tipo_doc = doc_type.capitalize()`
— pra rota `CONTATOS` isso é `tipo_doc="Contatos"`. `"Geral" != "Contatos"`
→ **zero chunks sempre**, para qualquer pergunta, independente da pessoa
perguntada. Corrigido via retag pontual (`dados/tmp/retag_chunks.py`, rodado
em 2026-07-21) — script identifica pelo próprio texto do chunk
(`[CONTATOS MOCK...]`, `[EDITAL MOCK...]`) e corrige `doc_type`/`tipo_doc`
dos chunks já indexados, sem precisar reingestão.

**Por que parecia "aleatório" (Dr. Fulano "funcionava", Dra. Ana Carvalho
"não"):** não era sobre a pessoa — nenhuma pergunta sobre ninguém funcionava
via rota `CONTATOS` (sempre 0 chunks, 100% determinístico dado o bug acima).
O que parecia aleatório era **qual rota o classificador escolhia** para cada
mensagem (ver item 5.1 — 3 classificadores LLM brigando, um deles falhando
o parse de JSON com frequência alta). Quando a mensagem caía em `GERAL`
(sem o filtro problemático) por acaso, a busca funcionava e retornava
QUALQUER pessoa cujo chunk tivesse mais similaridade semântica com aquela
frase específica — às vezes Fulano, às vezes Ana, às vezes Roberto Melo.
Dois bugs independentes (dado mal tagueado + classificação de rota
inconsistente) se combinando pareciam um único bug "aleatório" de IA, mas
os dois são 100% determinísticos e rastreáveis no código — não é "a IA
decidindo à toa".

---

## 5. Plano futuro — pipeline de roteamento + observabilidade

> Registrado a pedido do usuário em 2026-07-21, depois de resolver o crash
> do `crud_confirm`. Isto é uma PROPOSTA, não uma decisão tomada — nada aqui
> foi implementado ainda.

### 5.1 Unificar os classificadores de intenção

Hoje existem até 3 chamadas LLM independentes decidindo "o que fazer" com
uma mensagem, sem se coordenarem:

1. `router/llm_fallback.py::orchestrate()` — ação de alto nível.
2. `router/supervisor.py::rotear()` — 5 camadas próprias (regex/heurística/
   regex seeded/KNN/Flash), com override do Orquestrador por cima.
3. `agents/academic_knowledge/planning.py::criar_plano()` — o Planner (Pro)
   ainda decide o worker final por conta própria dentro da whitelist
   `VALID_WORKERS`, às vezes ignorando a rota já decidida.

Isso já causou pelo menos 2 bugs nesta rodada (dag_hint dessincronizado,
worker fantasma `crud_confirm`) e custa 2-3 chamadas Gemini por mensagem.

**Proposta (não decidida):** avaliar fundir (1) e (2) numa única chamada de
classificação — o Orquestrador e o Supervisor hoje respondem perguntas quase
idênticas ("qual é a intenção desta mensagem?") com vocabulários
diferentes. Um único schema Pydantic com a união de rotas/ações resolveria
de vez esse tipo de conflito de precedência. Fazer isso com cuidado: são
dois códigos com histórico de bugs sutis de HITL/memória dependentes da
ordem atual (ver docstring de `llm_fallback.py`) — não é refactor trivial,
merece sessão própria com testes de regressão nos 3 agentes ativos.

### 5.2 Observabilidade — Prometheus

Métricas que ajudariam a pegar esse tipo de bug antes de virar erro em
produção (hoje só existem métricas de latência/cache-hit/tokens):

- `oraculo_router_override_total{orchestrator_action, supervisor_rota}` —
  contador toda vez que o Orquestrador sobrescreve a rota do Supervisor.
  Teria mostrado o volume real desse conflito antes de virar bug visível.
- `oraculo_planner_worker_not_found_total{worker}` — contador quando
  `_despachar_workers` não acha um worker no registry (o que aconteceu
  silenciosamente com `crud_confirm` por sabe-se lá quanto tempo antes desta
  rodada).
- `oraculo_orchestrator_json_parse_failures_total` — contador dos "JSON
  Inválido" que aparecem toda hora no log (Gemini retornando prosa tipo
  "Here is..." em vez de JSON puro — sinal de que o `response_schema`/
  `response_mime_type` não está sendo respeitado com confiabilidade,
  provavelmente por causa do `max_output_tokens` baixo cortando a resposta
  no meio, ver "Unterminated string" no log de "Crud").
- `oraculo_rag_zero_chunks_total{doc_type}` — contador de buscas que
  retornam 0 chunks, quebrado por `doc_type`. Teria apontado o problema do
  item 4 acima imediatamente (100% das buscas `doc_type=contatos` dando 0).

### 5.3 Observabilidade — Grafana

Painel novo "Roteamento & Planner" no dashboard existente, com:

- Distribuição de rotas decididas por mensagem (stacked bar por rota/hora).
- Taxa de override do Orquestrador sobre o Supervisor (dado o contador 5.2).
- Taxa de falha de parse JSON do Orquestrador/Flash/Planner (os 3 usam o
  mesmo padrão `response_mime_type=application/json` + parse manual — se um
  falha por causa de `max_output_tokens` curto, os outros provavelmente
  também falham às vezes, só não apareceu ainda).
- Taxa de "0 chunks" por `doc_type` na busca RAG.

### 5.4 Logs — Docker/Celery

O log hoje é só stdout do container, sem correlação fácil entre serviços
(ex: seguir um `plan_id` do `oraculo_worker` até o `oraculo_evolution` exige
grep manual em dois containers diferentes). Propostas, por ordem de
esforço:

1. **Baixo esforço:** garantir que TODO log relevante (roteamento, planner,
   dispatch, delivery) sempre inclua `plan_id`/`session_id` no formato —
   hoje a maioria já inclui, mas alguns (ex: erros do Orquestrador) não.
2. **Médio esforço:** adicionar Loki + Promtail ao `docker-compose.yml` (já
   tem Prometheus/Grafana rodando) — os logs dos containers passam a ser
   consultáveis no próprio Grafana via LogQL, filtrando por `plan_id` sem
   precisar de `docker logs` manual em cada serviço.
3. Também vale corrigir o healthcheck dos workers Celery — hoje TODOS
   aparecem "(unhealthy)" no `docker ps` porque o healthcheck da imagem
   parece assumir um servidor HTTP na porta 9000 que só a `api` roda de
   verdade. Isso não afeta o funcionamento, mas mascara sinais reais de
   problema (não dá pra saber se um worker está realmente doente ou é só
   ruído do healthcheck errado).

### 5.5 Painel admin único (`/hub`) — levantamento factual (2026-07-21)

Usuário perguntou "o que são Router/Orquestrador no painel, posso ter um
painel pra eles" — levantamento do que EXISTE hoje (não é proposta, é
estado atual confirmado no código):

**Já existe e funciona:**
- `/hub` (`src/api/routers/web/hub.py`, login por cookie) — dashboard,
  liga/desliga por agente (`/hub/agents`, grava Postgres+Redis via
  `agent_config.py`), edição de prompt versionado por agente, gestão de
  usuários, audit log, simulador de chat, dashboard de avaliação RAG,
  chunkviz.
- `/hub/capabilities` só LISTA tools registradas em `capabilities/registry.py`
  — nenhuma tem consumidor vivo em produção hoje (decorativa).
- Catálogo de agentes é híbrido: lista de 4 agentes é HARDCODED em
  `agents/bootstrap.py` (autodiscovery seria "especulativo" pra só 4
  agentes, por comentário do próprio arquivo); Postgres (`agentes_catalogo`)
  só guarda enabled/disabled + prompt editável, não decide QUAIS agentes
  existem.

**Duplicação a limpar:** existe uma segunda API admin paralela
(`src/api/routers/admin/*`, auth por header `X-Admin-Key` estático, não
cookie) com funcionalidade sobreposta (usuários, audit, métricas de novo).
Dois sistemas de auth/admin fazendo parte da mesma coisa.

**NÃO existe hoje (gap real, não é só percepção do usuário):**
- Nenhum toggle de Router (`router/supervisor.py`) nem do Orquestrador
  (`router/llm_fallback.py::orchestrate()`) — só existe liga/desliga por
  AGENTE. Faz sentido a confusão: são peças de infraestrutura do pipeline
  sem representação nenhuma no painel hoje, e são literalmente os "2
  cérebros" do problema documentado na seção 5.1.
- RBAC (`ContextoPermissao`/`_PERMISSOES` em `domain/permissions.py`) é
  dicionário fixo no código-fonte — nenhuma tela edita isso.
- Redis: RedisInsight já roda (container separado, porta 8001) mas não
  está integrado/logado no hub — aba separada sem SSO.
- Postgres: nenhum admin, nem embutido nem separado (só `psql`).
- Logs: nenhuma visão centralizada no hub (ver 5.4 acima).

**Proposta pra próxima conversa (não decidida):** avaliar se `/hub` vira o
"ponto de ignição" único de verdade — unificar com `admin_api.py` (não dois
sistemas de auth), dar visibilidade real ao Router/Orquestrador (nem que
seja só um painel de leitura mostrando qual decidiu o quê por mensagem,
antes mesmo de ter toggle), trazer RBAC pra dentro do painel como
configuração editável, e embutir/linkar Redis+Postgres+logs no mesmo lugar
em vez de ferramentas espalhadas.

---

## 6. Scraping do wiki CTIC (DokuWiki) — reformulação completa (2026-07-22)

> Ver `arquitetura_oraculo.md` seção 11 pra visão arquitetural permanente.
> Aqui fica o histórico "como chegamos nisso" — bugs achados, decisões e o
> que ainda falta.

### Motivação

Scraper anterior (BeautifulSoup sobre o HTML **renderizado** de
`ctic.uema.br/wiki`, um DokuWiki) perdia hierarquia, quebrava tabelas em
texto corrido, ignorava PDFs anexados, e tinha um bug que crashava
(`context_label=` passado pro construtor de `ScrapedDocument`, mas
`context_label` é `@property` derivada, não campo do dataclass —
`TypeError` em runtime). Também existia uma SEGUNDA classe `UEMAWikiScraper`
morta/duplicada em `generic_scraper.py`, nunca registrada em lugar nenhum.

### Descoberta-chave: DokuWiki tem export nativo

Testado manualmente contra o site real antes de programar qualquer coisa:
- `doku.php?id={page}&do=export_raw` → devolve o **wikitext-fonte** da
  página (sintaxe `======`, `^|^`, `{{ }}`, `[[ ]]`), sem nav/sidebar/rodapé
  nenhum. Muito mais limpo que raspar HTML renderizado.
- `doku.php?do=index` → lista TODAS as páginas do wiki numa página só
  (namespaces majoritariamente flat — a maioria dos page_ids não tem
  hierarquia embutida, ex.: `almoxarifado`, `transferir_estoque_do_material`
  soltos, sem `sipac:almoxarifado:...`).

Consequência: a hierarquia (Portal → SIPAC → Almoxarifado → Tutorial) só
existe no **grafo de links** entre páginas, não no page_id — daí o módulo
`hierarchy.py` (ver arquitetura).

### O que foi implementado

Novo subpacote `src/infrastructure/scraping/implementations/dokuwiki/`
(`scraper.py`, `wikitext.py`, `hierarchy.py`, `media.py`, `discovery.py`) —
substitui o antigo `uema_wiki_scraper.py` (deletado) e a duplicata morta em
`generic_scraper.py` (removida). Registrado em
`scraping_service.py::build_default_scraping_service()` no lugar do antigo.

`ChunkerFactory.for_doc_type("wiki_ctic")` mudou de `semantic` (custava 1
embedding por sentença) para `markdown` (`MarkdownHeaderTextSplitter`) — o
wikitext convertido já tem headers/tabelas reais, não precisa detectar
breakpoint semântico.

Schema Redis `idx:rag:chunks` ganhou campos TAG `sistema`/`modulo`
(`redis_client.py::_schema_chunks()`), usados pra filtrar retrieval por
sistema institucional (ex: "responder só com contexto do SIPAC"). **Migração
ainda NÃO rodada** — precisa `FT.DROPINDEX idx:rag:chunks DD` + recriar
índice + reingestão completa, é destrutivo, fica esperando autorização
explícita antes de rodar em qualquer ambiente com dado real.

### Bug real achado só ao testar ao vivo (não previsto no plano)

`ScrapingService._ingest_to_rag()` só repassava `chunk.metadata` (dados do
chunker: `chunk_index`, `header_context`) pra `salvar_chunk()` — a taxonomia
do **documento** (`sistema`/`modulo`/`setor`/`tipo_doc`, calculada pelo
scraper) nunca chegava no Redis, ficava presa no meio do caminho. Sem esse
fix, o filtro por `sistema="SIPAC"` teria zero efeito (tudo cairia no
default "Geral"). Corrigido.

### Bug de encoding achado só ao testar ao vivo

`httpx` não detecta corretamente o charset da resposta de `do=export_raw`
(o header `Content-Type` não declara), e sem isso ele adivinha errado —
acentos viravam `M�dulo`/`Usu�rio` no conteúdo ingerido. Forçado
`r.encoding = "utf-8"` explicitamente em `DokuWikiScraper.fetch()`. **Se
algum dado acentuado aparecer bagunçado no futuro, checar isso primeiro
antes de suspeitar de outra coisa** — e checar se é mojibake real ou só o
terminal Windows (cp1252) exibindo errado (aconteceu as duas vezes nesta
sessão, causas diferentes).

### Decisão consciente: PDFs anexados NÃO são baixados/parseados

Testado contra `almoxarifado` (tem PDF "Apresentação do Módulo" anexado) —
usuário confirmou 2026-07-22 que os PDFs do wiki CTIC até agora são slides
de apresentação (pouco texto extraível, conteúdo procedural já coberto pela
própria página wiki). Decisão: em vez de baixar+parsear (`ParserFactory`),
o texto do chunk só ganha um link Markdown clicável direto pro arquivo
(`[Anexo PDF: nome.pdf](https://.../lib/exe/fetch.php?media=...)`), pro
usuário abrir manualmente se quiser. `media.py` ficou só com
`build_media_url()` (monta a URL) — a função de download+parse
(`baixar_e_extrair_pdf`) e o método `DokuWikiScraper.baixar_anexos_pdf()`
foram escritos e depois REMOVIDOS quando essa decisão saiu (não deixar
código morto). **Se um dia aparecer um PDF anexado que seja manual/texto
denso (não slide), reavaliar** — a infra de fetch da URL já existe, só
falta reconectar o parser se for preciso.

### Bug encontrado e corrigido no `/hub` (chunkviz), fora do escopo original

`hub.py::cv_extract_url` (botão "extrair de URL" do chunkviz) tinha dois
problemas pré-existentes, achados só ao tentar testar manualmente pela UI:
1. Chamava `save_temp_file(file_id, ...)` sem extensão no nome — `ext=""`
   não bate em `ALLOWED` → sempre estourava `Formato '' não suportado`,
   pra QUALQUER url, não só a do wiki.
2. Tinha `GenericHTTPScraper` hardcoded — mesmo corrigindo (1), continuaria
   testando o scraper genérico antigo, não o `DokuWikiScraper` novo.

Corrigido: roteia por domínio (`ctic.uema.br` → `DokuWikiScraper`, resto →
`GenericHTTPScraper`, igual `ScrapingService._resolve()` já faz) e usa
`save_temp_file()` do jeito certo (deixa ele gerar o próprio `file_id`).

### Eval automatizado

`tests/eval/test_ctic_wiki_eval.py` (9 casos) + fixtures reais congeladas em
`tests/fixtures/ctic_wiki/*.txt` (baixadas 1x do site real via
`do=export_raw`). Cobre: conversão wikitext→Markdown, detecção de PDF como
link, resolução de hierarquia sistema/modulo via grafo, propagação de
taxonomia até `salvar_chunk()`, fidelidade do chunker `markdown`. Suíte
completa (`tests/unit` + `tests/eval`) rodada após cada mudança — 190+
passando, as 5 falhas pré-existentes (Redis local fora do ar,
`test_registration_repository.py`, `test_sigaa_eval.py`) não têm relação
com esta mudança.

### Pendente (esperando autorização do usuário)

1. Migração destrutiva do schema Redis (`FT.DROPINDEX ... DD` + recriar +
   reingerir) pra `sistema`/`modulo` passarem a existir de verdade no
   índice — hoje só existem no código, não no Redis.
2. Rodar a descoberta em massa (`discovery.py::descobrir_paginas()`, via
   `do=index`) contra o site real pra popular a fila de scraping com todas
   as páginas do wiki — feito só manualmente/pontual até agora (4 páginas de
   teste), não em lote.
3. Testar o fluxo completo pelo `/hub` chunkviz manualmente (usuário estava
   nisso quando parou pra pedir essa atualização de notas).

---

## 7. Experimento LangGraph (branch/worktree `langgraph`) — dois bugs estruturais achados e corrigidos (2026-07-27/28)

> Esta branch existe só pra testar se o LangGraph consegue rodar de verdade no
> Oráculo (ver `.claude.md` "Sem LangGraph" no `main` — descartado antes por
> ter "travado em state/builder"). Ativado trocando o import em
> `process_message_task.py` pra `dispatcher_langgraph.processar` (ver
> docstring do próprio arquivo). Rotas cobertas: `TICKET_ABERTURA`, `GERAL`,
> `CALENDARIO`, `EDITAL`, `CONTATOS`, `WIKI`. Tudo mais (SIGAA, CRUD,
> comandos) continua 100% no pipeline original.

### 7.1 Bug 1 — `Event loop is closed` / `Future attached to a different loop` (corrigido)

**Sintoma:** toda mensagem que caía numa rota LangGraph tinha chance de
quebrar com `RuntimeError: Event loop is closed`, `Task ... got Future
attached to a different loop`, ou `RedisVLError: Failed to load data: Event
loop is closed` — de forma aparentemente aleatória (às vezes a mesma rota
funcionava, às vezes não).

**Causa raiz:** `dispatcher_langgraph.py` guardava o `AsyncRedisSaver`
(checkpointer) como singleton de módulo (`_graph`/`_saver_cm`), criado uma
vez por processo Celery. Só que cada task Celery (`process_message_task.py`)
chamava `asyncio.run(...)` — um event loop **novo** a cada mensagem. O
`AsyncRedisSaver` nascia sob o loop da 1ª mensagem que batesse numa rota
LangGraph; quando esse loop fechava (fim do `asyncio.run()`), a conexão
Redis ficava presa a um loop que não existe mais — qualquer mensagem
seguinte que reusasse essa conexão quebrava.

**Investigação:** duas tentativas de patch local (cache do saver por
event loop via `WeakKeyDictionary`, depois abrir/fechar a conexão a cada
chamada) trocavam um sintoma pelo próximo (a 1ª virou vazamento de conexão —
`ConnectionError: Connection closed by server` — até o Redis derrubar as
conexões acumuladas) sem atacar a causa estrutural. Descartadas a pedido do
usuário ("não quero soluções temporárias").

**Fix aplicado (estrutural, sem trocar o modelo Celery):** event loop
**persistente por processo worker**, criado uma vez em `worker_process_init`
e reusado por todas as tasks daquele processo via
`run_in_worker_loop()` (substitui `asyncio.run()`) — ver
`src/infrastructure/celery_app.py`. Fechado corretamente em
`worker_process_shutdown` (signal novo, dispara por processo filho do
prefork, diferente de `worker_shutdown` que só dispara uma vez no processo
principal). Isso permite voltar o `dispatcher_langgraph.py` ao singleton
simples original (`_graph`/`_saver_cm`), que agora é correto porque só existe
1 loop por processo — a conexão nunca mais atravessa a fronteira de um
`asyncio.run()` porque essa fronteira deixou de existir nos dois entry
points que chegam no grafo (`processar_mensagem_task`,
`processar_mensagem_whatsapp`). Rede de segurança extra: `worker` roda com
`--max-tasks-per-child=500` (`docker-compose.yml`) pra reciclar o processo
periodicamente, já que o loop agora sobrevive indefinidamente.

**Validado em produção:** 10+ mensagens seguidas em rotas LangGraph, mesma
sessão e sessões diferentes, sem nenhuma ocorrência dos três erros.

**Roteiro registrado pra depois (não implementado, só documentado):** se o
experimento for validado pra produção E o volume justificar concorrência
real, a arquitetura recomendada é um "Graph Runtime Service" dedicado
(processo async-nativo próprio, não um worker Celery) reaproveitando
`src/infrastructure/message_stream.py` (XADD/XACK/XPENDING — hoje código
morto, não usado no caminho real de produção) e o container `worker_graph`
(hoje provisionado mas ocioso). Trade-off: perde retry/Flower automáticos do
Celery nessa rota, reativa infra nunca testada sob carga real — só vale o
investimento quando o LangGraph já tiver provado valor suficiente.

### 7.2 Bug 2 — funil de ticket (HITL) quebrava sempre na 2ª pergunta seguinte (corrigido)

**Sintoma:** o funil de ticket (`Abra um ticket` → 4 perguntas sequenciais
via `interrupt()`) funcionava pra 1ª pergunta → 2ª pergunta, mas na
transição da 2ª → 3ª pergunta o sistema agia como se não houvesse nenhum
`interrupt()` pendente (`state.next` vazio) e tratava a resposta do usuário
como mensagem solta nova, abandonando o ticket no meio. Reproduzido 2x de
forma idêntica.

**Investigação:** confirmado por pesquisa na documentação oficial do
LangGraph que múltiplos `interrupt()` sequenciais no MESMO node (como
`ticket_node` original tinha — 4 empilhados) é um padrão suportado, matching
por índice de chamada. Só que a busca por issues abertas no pacote real
(`langgraph-checkpoint-redis`, o checkpointer Redis) achou o problema exato:
bugs conhecidos na resumption de múltiplos interrupts pendentes,
especificamente com checkpointer Redis (funciona com `InMemorySaver`, quebra
com Redis) — [langchain-ai/langgraph#5074](https://github.com/langchain-ai/langgraph/issues/5074),
[redis-developer/langgraph-redis#133](https://github.com/redis-developer/langgraph-redis/issues/133)
(sintoma quase idêntico: "funciona na 1ª confirmação, quebra na 2ª"). Versão
instalada (`langgraph-checkpoint-redis==0.5.1`) já era a mais recente
disponível — não tinha upgrade trivial pra sair do bug.

**Fix aplicado:** `ticket_node` (1 node, 4 `interrupt()`s) virou 5 nodes
separados (`ticket_ask_tipo` → `ticket_ask_categoria` → `ticket_ask_queixa`
→ `ticket_confirm` → `ticket_save`, ligados por edges condicionais em
`langgraph_experiment/graph.py`), cada um com exatamente 1 `interrupt()` —
reduz a dependência à trilha mais simples/testada do checkpointer (1
interrupt pendente por vez), sem trocar de checkpointer. `ticket_save`
separado de `ticket_confirm` de propósito (nó de aprovação separado do nó
de efeito colateral, idempotência — princípio já registrado na curadoria do
`.claude.md`, link note.com sobre HITL).

**Bug relacionado corrigido junto:** o código antigo aceitava qualquer texto
como resposta (`"Incidente" if resposta == "1" else "Requisicao"` —
responder a palavra "Incidente" por extenso virava "Requisicao" em
silêncio). Cada node novo agora valida a resposta e, se inválida,
re-pergunta (edge condicional de volta pro mesmo node) em vez de aceitar
qualquer coisa.

**Também mudou:** `OraculoState` (`langgraph_experiment/state.py`) de
`TypedDict` pra Pydantic `BaseModel` (validação em runtime, alinhado com a
regra de tipagem do `.claude.md`). `ticket_data` ficou como `dict` (não um
`BaseModel` aninhado) porque o LangGraph avisou que tipos Pydantic
customizados aninhados exigem registro explícito no serializer
(`allowed_msgpack_modules`) e isso vai virar erro bloqueante em versão
futura — dict é nativo, sem esse risco.

**Validado:** 3 testes de regressão novos
(`tests/unit/application/test_langgraph_ticket_hitl.py`, rodando com
`MemorySaver` — isolados do bug de infra do Redis) cobrindo fluxo completo
válido, re-pergunta em resposta inválida, e cancelamento. Testado também
manualmente contra o `AsyncRedisSaver` real (o checkpointer de produção) e
depois via WhatsApp de ponta a ponta — aprovado pelo usuário em 2026-07-28.

`langgraph-checkpoint-redis` pinado em `==0.5.1` no `requirements.txt` (era
`>=0.4.0` flutuante) — área comprovadamente instável, não deixar subir de
versão sem testar de novo.

### 7.3 Arquivos tocados nesta rodada

- `src/infrastructure/celery_app.py` — loop persistente por processo
  (`run_in_worker_loop`), signal `worker_process_shutdown` novo.
- `src/application/tasks/process_message_task.py` — 2 entry points trocados
  pra `run_in_worker_loop` (os 2 que chegam no LangGraph); os outros 2
  (`enviar_resposta_whatsapp_task`/`enviar_aviso_latencia_task`) continuam
  com `asyncio.run()` puro, sem mudança.
- `src/application/runtime/dispatcher_langgraph.py` — voltou ao singleton
  simples original + `aclose_graph()` pro shutdown + docstring com as
  issues do checkpointer.
- `langgraph_experiment/state.py` — `TypedDict` → Pydantic `BaseModel`.
- `langgraph_experiment/nodes.py` — `ticket_node` quebrado em 5 nodes +
  validação de resposta por node.
- `langgraph_experiment/graph.py` — edges novos ligando os 5 nodes do
  funil de ticket.
- `docker-compose.yml` — `--max-tasks-per-child=500` no serviço `worker`.
- `requirements.txt` — `langgraph-checkpoint-redis` pinado.
- `.claude.md` — nova entrada de curadoria com os dois achados (loop
  persistente + bug do checkpointer), pra não redescobrir do zero numa
  sessão futura.
- `tests/unit/application/test_langgraph_ticket_hitl.py` — novo, 3 testes.

### 7.4 Pendências / não feito nesta rodada (registrado, não esquecido)

- Graph Runtime Service dedicado (ver 7.1) — só se/quando o experimento for
  validado pra produção.
- Avaliar `AsyncPostgresSaver` como alternativa mais madura ao checkpointer
  Redis, SE o bug do item 7.2 voltar de outra forma no futuro (projeto já
  usa Postgres via `asyncpg`/`sqlalchemy` — não avaliado nesta rodada por
  decisão consciente, o redesenho dos nodes já resolveu o sintoma
  observado).

---

## 8. Experimento LangGraph — vazamento de estado entre execuções, "detour" institucional, linguagem natural e fluxo CRUD novo (2026-07-28)

> Continuação do item 7. Testado a fundo via WhatsApp com transcript real
> colado pelo usuário — achou um bug crítico novo e confirmou uma limitação
> de UX real, além de um bug separado (não-LangGraph) no ChunkViz.

### 8.1 Bug — vazamento de estado entre execuções sucessivas do mesmo funil na mesma sessão (corrigido)

**Sintoma (achado no transcript real):** um 1º ticket foi aberto e confirmado
("Sem wifi" → `Tipo: Incidente / Categoria: Rede e Conectividade`). Um 2º
ticket na MESMA sessão, com o usuário respondendo **errado** de propósito
tipo e categoria, mesmo assim teve o resumo final mostrando
`Tipo: Incidente / Categoria: Rede e Conectividade` **idênticos ao 1º
ticket** — dado velho vazando pro ticket novo.

**Causa raiz:** `_thread_config()` usa um `thread_id` fixo por sessão pra
sempre; o LangGraph mantém o checkpoint desse `thread_id` indefinidamente,
mesmo depois do grafo chegar em `END`. Ao iniciar um funil NOVO, o payload
do `ainvoke()` não resetava `ticket_data`/`ticket_error`/`ticket_confirmed`
— o LangGraph mescla o dict parcial em cima do último checkpoint salvo, e
os valores do funil anterior continuavam vivos. As edges de validação (só
checavam "o campo tem *algum* valor?") eram enganadas por esse dado velho,
deixando passar de pergunta mesmo com resposta atual inválida.

**Fix:** `dispatcher_langgraph.py::_reset_payload_para_rota()` — ao iniciar
um funil novo (não ao retomar um pendente), o payload inicial reseta
explicitamente os campos DAQUELE funil (`ticket_*` ou `crud_*`, conforme a
rota), sobrescrevendo qualquer resíduo de execução anterior no mesmo
`thread_id`.

### 8.2 UX — "detour" institucional durante ticket/CRUD (implementado)

**Sintoma:** pergunta institucional no meio do funil de ticket ("me conte a
história da UEMA") era tratada como resposta inválida, em vez de ser
respondida — o funil não conseguia "pausar pra responder e retomar depois".

**Fix (sem reconstruir o grafo pra "conversas paralelas" de verdade — mais
risco em cima de um checkpointer Redis já frágil, sem necessidade real):**
filtro leve em `dispatcher_langgraph.py::processar()`, ANTES de consumir a
mensagem como `Command(resume=...)`. Descobre o node pendente
(`state.next[0]`), roda o validador daquele passo (reaproveitado do próprio
node via `nodes.VALIDATORS_POR_NODE` — sem duplicar regra); se a resposta
não validar, reclassifica com `rotear()` (Supervisor real); se for uma rota
RAG direta (`GERAL`/`CALENDARIO`/`EDITAL`/`CONTATOS`/`WIKI` — decisão:
detour NÃO cobre SIGAA/outras rotas ambíguas), responde via
`nodes.responder_rag_direto()` (mesma busca+síntese que `rag_node` usa) e
reapresenta a pergunta pendente (extraída de
`aget_state().tasks[0].interrupts[0].value["question"]` — não precisou de
campo de estado novo pra isso, já vem do próprio LangGraph) — SEM tocar o
grafo/interrupt, o funil fica exatamente onde estava.

### 8.3 UX — linguagem natural em vez de formato rígido (implementado)

Categoria só aceitava dígito exato, confirmação só aceitava `sim/s/confirmo`
ou `não/nao/n` literais. `langgraph_experiment/nodes.py` ganhou validadores
com sinônimos/regex (`validar_tipo`, `validar_categoria`, `validar_confirmacao`,
`validar_campo_crud`, `validar_valor_crud`) — ex: "Hardware", "pode enviar",
"deixa pra lá" agora são aceitos.

### 8.4 Fluxo novo: CRUD de cadastro via LangGraph (implementado)

Mesmo escopo do `crud_tool.py` original (`src/agents/tickets/crud_tool.py`)
— só `centro`(setor)/`telefone`, não expandido. 4 nodes novos
(`crud_ask_campo` → `crud_ask_valor` → `crud_confirm` → `crud_save`), mesmo
padrão 1-interrupt-por-node do ticket. Reaproveita
`ticket_repository.atualizar_setor_e_telefone()` (escrita real) e
`settings.DEV_TEST_NO_DB_WRITE`/`dev_dump.salvar_json_dev` (mesmo gate
dev/prod já usado em todo o projeto) — nenhuma lógica de persistência nova.
**Melhoria em relação ao original:** `crud_ask_valor` valida o setor contra
`CentroEnum` de verdade (o `crud_tool.py` original aceita texto livre sem
validar contra o enum, potencial erro de banco silencioso) — corrigido só
na versão LangGraph, sem tocar o `crud_tool.py` antigo.

### 8.5 Bug registrado como TODO (não corrigido, fora do escopo LangGraph): ChunkViz sempre seleciona Docling

Reportado pelo usuário com log de erro real (workers `SIGKILL`ados durante
pre-load de ML). Investigado: **não existe nenhuma flag de configuração no
projeto pra desativar Docling** (`settings.py` não tem nada assim).
`src/rag/ingestion/parser_factory.py::_EXT_TO_PARSERS`/`ParserFactory.auto()`
tem `"docling"` **hardcoded** como 1ª opção pra `.pdf`/`.docx`
(`parser_factory.py:117-131,204-212`). A única forma de "desativar" hoje é
desinstalar o pacote `docling` (nem está em `requirements.txt` — instalação
manual), disparando fallback por `ImportError` pra `pymupdf`.
`chunkviz_tools.py` (usado pelo `/hub`) chama `ParserFactory.auto()`/`.get()`
direto — mesmo bug. Causa provável: ou o pacote ainda está instalado no
ambiente, ou `DoclingAdapter()` lança exceção que não é `ImportError`/
`ValueError` (únicas capturadas no fallback) e sobe sem rede de segurança.
**Proposta não implementada:** adicionar `settings.DISABLE_DOCLING`, checado
em `_EXT_TO_PARSERS`/`auto()` antes de incluir `"docling"` nos candidatos.

### 8.6 Observado, não confirmado: workers `SIGKILL`ados durante pre-load de ML

Log do usuário mostrou `ForkPoolWorker` sendo morto (`signal 9`)
repetidamente durante "Pre-loading ML models on process init", timeout
esperando UP message. Possível relação com `--max-tasks-per-child=500`
(item 7.1) — mais respawns de processo = mais recargas do CrossEncoder =
mais pressão de memória no limite de 768M do container `worker`. Não
investigado a fundo nesta rodada — registrado pra acompanhar.

### 8.7 Arquivos tocados nesta rodada

- `langgraph_experiment/state.py` — `crud_data`/`crud_error`/`crud_confirmed`.
- `langgraph_experiment/nodes.py` — validadores extraídos como funções puras
  reaproveitáveis (node + detour), linguagem natural, 4 nodes de CRUD,
  `responder_rag_direto()` extraído de `rag_node` pro detour reaproveitar,
  `VALIDATORS_POR_NODE` (registry pro filtro de detour do dispatcher).
- `langgraph_experiment/graph.py` — edges do CRUD, `classify` com 3ª opção.
- `src/application/runtime/dispatcher_langgraph.py` — reset de estado por
  fluxo, filtro de detour, rota `CRUD` mapeada.
- `tests/unit/application/test_langgraph_ticket_hitl.py` — casos novos
  (linguagem natural, vazamento de estado, detour institucional).
- `tests/unit/application/test_langgraph_crud_hitl.py` — novo.

## 9. Sessão 2026-07-31 — reavaliação do LangGraph, RBAC, comando de saída, bug do `cancelado` vazando, limpeza de workers mortos

Contexto: usuário retestou o experimento LangGraph extensivamente via
WhatsApp e não achou mais tão nocivo quanto da rejeição original (`.claude.md`
linha 11 histórica). Sessão focou em: (1) validar isso com critério técnico
antes de atualizar a decisão registrada, (2) fechar duas lacunas concretas
achadas no caminho (RBAC ausente no funil LangGraph, sem jeito de sair do
HITL), (3) rodar os dois testes direcionados que ficaram pendentes da rodada
anterior (múltiplos `interrupt()` no mesmo node; carga concorrente), (4)
limpeza de código morto identificado ao longo do processo.

### 9.1 Webhook mudo — causa raiz não tinha nada a ver com LangGraph

Sintoma relatado: bot parou de responder após `git pull` + `docker compose up
-d --force-recreate` numa sessão de trabalho. Causa: `WHATSAPP_HOOK_URL` no
`.env` apontava pra `http://api:9000/webhook` — a rota real é
`POST /webhook/evolution` (prefixo `/webhook` + `@router.post("/evolution")`
em `webhook_controller.py`). `EvolutionService` reconfigura o webhook no
Evolution a cada boot do `api`, então qualquer recreate reafirmava a URL
errada. Corrigido no `.env` (não versionado).

Achado secundário no mesmo diagnóstico: todo serviço do `docker-compose.yml`
ganhou `profiles:` (`core`/`monitoring`/`app`/`gateway`) num commit anterior
(`e1cd34f`, o mesmo que já se descrevia como WIP), mas nada ativa isso por
padrão — `docker compose up -d` sem `--profile` não sobe nada. Não corrigido
nesta rodada (ver `.claude.md` pra o workaround).

### 9.2 RBAC ausente no funil LangGraph (corrigido)

`checar_permissao_chamado()` (`src/agents/tickets/rbac.py`) já existia e já
protegia o fluxo real (`ticket_flow.py`/`crud_tool.py`), mas nunca foi
portado pro `langgraph_experiment/nodes.py` — qualquer role/status
conseguia abrir ticket/CRUD via LangGraph. Adicionado no topo dos nodes de
entrada `ticket_ask_tipo`/`crud_ask_campo`. Testado com
`DEV_TEST_SKIP_REGISTRATION=False` e telefone sem cadastro: bloqueou antes
de qualquer pergunta.

### 9.3 Comando de saída do HITL (implementado)

Não existia jeito de sair de um funil de ticket/CRUD no meio — mensagens
como "sair"/"cancelar" não validavam pro passo pendente e caíam no filtro
de detour institucional (`dispatcher_langgraph.py`), que tentava RAG,
respondia "não encontrei" e repetia a mesma pergunta pendente indefinidamente
(bug reproduzido em teste real via WhatsApp). Implementado: campo
`state.cancelado` (`langgraph_experiment/state.py`), checado em toda edge
condicional ANTES de qualquer outra regra, e `_eh_saida()`/`_resultado_saida()`
(`nodes.py`) checado em todo node do funil, com prioridade sobre o
validador do node e sobre o detour no dispatcher. Comando reconhecido só
como mensagem EXATA (`^sair$`, `^cancelar$`, etc., regex com âncoras) —
decisão consciente pra não capturar a palavra solta dentro de texto livre
(ex: `ticket_ask_queixa` aceita descrição livre do problema).

### 9.4 Bug real: `cancelado=True` vazando entre execuções (corrigido)

Sintoma: depois de UMA sessão digitar "sair" uma vez, todo ticket/CRUD
seguinte NESSA MESMA sessão quebrava — aceitava a 1ª resposta (ex: tipo)
e ia direto pro fim, sem perguntar categoria/queixa/confirmação. Parecia
exatamente o bug de concorrência do checkpointer (2º resume "perdendo" o
`next`), mas não era. Causa raiz: `_reset_payload_para_rota()`
(`dispatcher_langgraph.py`) resetava `ticket_data`/`ticket_error`/
`ticket_confirmed` ao iniciar um funil novo, mas não o novo campo
`cancelado` — que fica gravado no checkpoint indefinidamente (mesmo padrão
de fundo do bug 8.1). Toda edge condicional checa `state.cancelado` antes
de qualquer regra, então uma vez `True`, fica `True` pra sempre nessa
sessão. Fix: `cancelado: False` adicionado ao payload de reset. Confirmado
contra dado real de produção (state history da sessão mostrava `ticket_data`
capturado certo mas `next=()` imediato) e retestado com sucesso depois do
fix (3 resumes seguidos avançando corretamente).

**Lição registrada:** se esse sintoma reaparecer (resposta válida não avança
o funil, `state.next` some), checar primeiro se é o mesmo padrão de leak
(campo de estado novo esquecido no reset) antes de suspeitar do
checkpointer de novo.

### 9.5 Dedup de webhook por `msg_key_id` (corrigido)

Achado ao investigar por que uma sessão real ficou com dados inconsistentes:
o Evolution reentrega o mesmo evento de webhook com frequência alta —
confirmado em produção, praticamente toda mensagem chegava 2x. Sem dedup,
cada duplicata virava uma task Celery independente mexendo no mesmo funil
HITL por conta própria (ex: a mesma resposta processada 2x, uma delas
respondendo a pergunta ERRADA do funil por já ter avançado). Fix:
`webhook_controller.py` deduplica por `msg_key_id` (id único que o Evolution
já manda) usando `acquire_lock()` de `redis_client.py` — função que já
existia com esse propósito exato e nunca tinha sido chamada em lugar
nenhum. TTL de 120s. Testado com payload idêntico enviado 2x (1ª aceita, 2ª
ignorada) e a dedup pegou uma duplicata real acontecendo durante o teste.

### 9.6 `langgraph_experiment/` sem volume mount (corrigido)

`docker-compose.yml` só montava `./src` como volume — `langgraph_experiment/`
nunca chegava aos containers (rodavam a cópia congelada da imagem). Todo o
trabalho de 9.2/9.3/9.4 só passou a valer de verdade nos containers reais
depois desse fix (adicionado ao anchor `x-worker-base` e ao serviço `api`).

### 9.7 Dois testes direcionados que faltavam da rodada anterior (fechados)

**Múltiplos `interrupt()` no mesmo node**: node descartável (2 `interrupt()`
sequenciais numa única execução, fora dos nodes de produção) contra o
checkpointer real. Não reproduziu o bug catastrófico dos issues #5074/#133
— fluxo completou corretamente end-to-end nos 2 resumes. Achado menor:
`aget_state().next` reportou vazio logo após o 1º resume mesmo com um 2º
interrupt pendente (inconsistência de relatório, sem impacto — confirmado
pelo 2º resume funcionar). Versões no momento do teste: `langgraph==1.2.10`,
`langgraph-checkpoint-redis==0.5.1`, `langgraph-checkpoint==4.1.1`.

**Carga concorrente**: 5 processos separados (loop persistente cada,
imitando `run_in_worker_loop()`) rodando o funil completo em paralelo,
sessões diferentes — zero erros de event loop. Teste extra mais agressivo:
2 respostas concorrentes pro MESMO interrupt pendente, sem lock nenhum —
não quebrou, mas causou last-write-wins silencioso (uma resposta some sem
aviso). Confirma que o lock por telefone (`lock:msg:{phone}`,
`process_message_task.py`) é necessário, não redundante.

**Conclusão**: os dois motivos técnicos concretos da rejeição original
parecem resolvidos no upstream. Falta só RBAC testado corretamente na
`main` (fora do LangGraph) antes de reconsiderar promover pra lá — decisão
consciente de NÃO mexer nisso nesta sessão.

### 9.8 Limpeza de código morto/ocioso

- **Worker fantasma `crud_confirm`**: nunca teve `@register()` em lugar
  nenhum (já documentado antes, nunca limpo). Removida a entrada de
  `_QUEUES` (`application/workers/registry.py`); comentários repetidos em
  `supervisor.py`/`planning.py` reduzidos a ponteiro pra
  `agents/tickets/service.py` (mantém o histórico completo).
- **`worker_graph`/`graph_extractor`**: confirmado por grep independente
  (não só pela nota antiga) — zero chamadores reais em router/agents/
  use_cases/commands/api. Container removido do `docker-compose.yml` e
  parado; código (`worker_graph_extractor.py`, registro em
  `celery_app.py`/`registry.py`) mantido intacto pra reativar se aparecer
  uso real.
- **`message_stream.py` — QUASE removido por engano**: a entrada anterior
  deste arquivo (seção 7.1) dizia "código morto, não usado no caminho real
  de produção". Verificação direta no código atual mostrou o oposto:
  `_xack_stream()` roda em toda mensagem processada, `recover_pending_messages()`
  roda no boot do worker E na task periódica `stream_recovery` (observada
  rodando com sucesso nos logs desta própria sessão). Não removido — a nota
  antiga estava errada ou fora de escopo (provavelmente só válida pro
  cenário hipotético do "Graph Runtime Service" nunca implementado).
  **Lição**: verificar contra o código atual antes de agir em cima de uma
  nota antiga, mesmo quando parece autoritativa.
- **Mantidos sem mudança**: `ytb_download`/`insta_download` — tecnicamente
  funcionais (regex de detecção em `router/supervisor.py`, dispatch real em
  `dispatcher.py`), decisão consciente de manter mesmo fora do escopo
  "acadêmico" da identidade do produto.

### 9.9 Arquivos tocados nesta rodada

- `.claude.md` — status do LangGraph atualizado 4x ao longo da sessão
  (reavaliação inicial → achados testados → resultado dos 2 testes
  direcionados), volume mounts e profiles do compose documentados.
- `langgraph_experiment/state.py` — campo `cancelado`.
- `langgraph_experiment/nodes.py` — RBAC nos nodes de entrada,
  `_eh_saida()`/`_resultado_saida()`, checagem de `cancelado` em toda edge.
- `langgraph_experiment/graph.py` — `"__end__"` adicionado como destino nas
  edges que ainda não tinham.
- `src/application/runtime/dispatcher_langgraph.py` — prioridade do comando
  de saída sobre validador/detour; reset de `cancelado` no payload novo.
- `src/application/webhook/webhook_controller.py` — dedup por `msg_key_id`.
- `src/application/tasks/ingestion_tasks.py` — bug não relacionado ao
  LangGraph, corrigido na mesma sessão: `_extrair_texto()` ignorava o
  parser escolhido no ChunkViz, sempre usava `ParserFactory.auto()`.
- `docker-compose.yml` — volume mount de `langgraph_experiment/`, remoção
  do serviço `worker_graph`.
- `src/application/workers/registry.py`, `src/router/supervisor.py`,
  `src/agents/academic_knowledge/planning.py`, `src/capabilities/registry.py`
  — limpeza das referências ao worker fantasma `crud_confirm`.

### 9.10 Pendências explícitas pra próxima sessão

1. RBAC testado corretamente na `main` (fora do LangGraph) — bloqueia a
   decisão de promover o LangGraph.
2. Decisão de estratégia de merge/integração da branch `langgraph` — ainda
   não tomada, e não deve ser até o item 1 fechar.
3. `GEMINI_MODEL=gemini-3.1-flash-lite-preview` no `.env` dando 404 nos
   testes de detour — nome de modelo provavelmente inválido/descontinuado.
4. `COMPOSE_PROFILES` sem default — decidir se fixa no `.env` ou documenta
   o comando completo.
5. Containers `beat`/`worker`/`worker_media`/`worker_rag`/`worker_synthesis`
   aparecendo "unhealthy" no `docker compose ps` há um tempo — não
   investigado, pode ser só o healthcheck script.
6. Dúvida do usuário sobre "OCR do SIGAA" não resolvida — não achado
   arquivo dedicado nessa área, suspeita de ser o `rapidocr_adapter.py`
   genérico (não específico de SIGAA).
7. Convenção de branches (`feature/`, `fix/`, `spike/`, `research/`) —
   discutida, ainda não formalizada como prática.

## 10. Sessão 2026-07-31 — início do laboratório REST (`rest_lab/`), branch `research/rest-mcp-estudos`

Contexto: nova worktree isolada (`Oraculo-rest-mcp`), branch
`research/rest-mcp-estudos`, criada a partir de `langgraph` — carrega todo o
histórico da seção 9, mas é um esforço à parte, sem relação com o LangGraph.
Objetivo explícito do usuário: estudo/prática de API REST e MCP como prova
de capacidade técnica pra apresentar na CETIC/UEMA, **não** é feature de
produto e **não** integra com `src/` do núcleo por enquanto. Primeiro passo
combinado: REST puro (GET/POST/PUT/DELETE) contra APIs públicas sem
fricção — MCP fica pra uma rodada seguinte.

### 10.1 Achado relevante antes de implementar: Oráculo não tem tool-calling agentic em lugar nenhum

Ao decidir como o "agente" deveria escolher qual chamada REST fazer,
levantamento no código mostrou que o padrão de decisão via LLM hoje em
produção é **sempre saída estruturada forçada** (`response_schema` Pydantic
via `google.genai`, ver `src/router/llm_fallback.py::_classificar_com_flash`/
`orchestrate`), nunca function-calling nativo. O único código que já tentou
esse padrão é `src/capabilities/messaging/gmail_tool.py`
(`langchain_core.tools.StructuredTool` + comentário citando `bind_tools`) —
mas é código morto: nenhum lugar do projeto importa/chama essas factories
(confirmado via grep, único outro hit é `application/chain/oracle_chain.bak`,
arquivo `.bak` nunca executado). Mesma situação do achado já registrado na
seção 9 sobre `crud_tools`/`capabilities/registry.py` — capability
implementada, sem consumidor vivo. **Decisão pra esta rodada**: não
resolver isso agora — `rest_lab/router.py` usa regex determinístico (mesmo
nível de `langgraph_experiment/nodes.py::classify_node`), sem o LLM
decidindo a chamada. Function-calling real fica registrado como evolução
natural de uma próxima rodada, e só faria sentido introduzir via
`google.genai` (o provider já usado no projeto), não via LangChain
`bind_tools` (padrão comprovadamente não adotado aqui apesar de já ter sido
tentado uma vez).

### 10.2 Estrutura criada — `rest_lab/`

Pasta nova, sibling de `langgraph_experiment/`, sem depender de `src/`
(as três APIs usadas são públicas, sem autenticação, então nem
`settings`/`.env` são necessários — diferente de `langgraph_experiment/`,
que depende pesado do núcleo real):

- `clients.py` — um `httpx.AsyncClient` por API (JSONPlaceholder, DummyJSON,
  httpbin) como singleton lazy, mesmo espírito do `_get_client()` de
  `llm_fallback.py`. `fechar_todos()` chamado só na saída do CLI.
- `tools.py` — uma função async por operação REST, retorno sempre
  `{"mensagem": str}` (mesmo formato de
  `capabilities/tools/tool_get_student_info.py`, reforça o hábito de já
  devolver texto pronto pra chat, não dado cru). Erro de rede/HTTP sempre
  capturado dentro da função (nunca sobe como exceção) — mesmo padrão
  defensivo do núcleo.
- `router.py` — `rotear(mensagem) -> dict`, regex puro mapeando frase →
  tool. Único ponto de decisão "qual chamada fazer" (ver 10.1 sobre o
  porquê de não ser LLM nesta rodada).
- `run_test.py` — CLI (`python3 -m rest_lab.run_test`), loop de `input()`
  sem WhatsApp/Celery/dispatcher, espelhando a forma de
  `langgraph_experiment/run_test.py` (não reaproveita código, mesma
  estrutura só).

Operações implementadas e testadas manualmente via CLI, todas OK:
- JSONPlaceholder: `listar_usuarios` (GET, lista formatada, cap de 10
  itens pensando em WhatsApp), `obter_usuario` (GET com 404 tratado),
  `criar_post` (POST), `atualizar_post` (PUT), `deletar_post` (DELETE).
- DummyJSON: `listar_produtos` (GET com paginação real, mostra `total` do
  payload), `buscar_produto` (GET busca textual).
- httpbin: `testar_status` (GET `/status/{code}`, sem `raise_for_status`
  de propósito — o objetivo é justamente observar status ≠ 200 sem virar
  exceção), `echo_request` (GET `/get`, mostra headers/args ecoados).

**Nota sobre JSONPlaceholder**: POST/PUT/DELETE não persistem de verdade no
servidor — é comportamento documentado da própria API pública (sempre
responde 200/201 com eco do payload + id fake), não bug do `rest_lab`.
Confirmado no teste manual: `criar_post` devolveu `id=101` (a API sempre
usa 101 pra novos posts, já que a "base" tem 100 fixos).

### 10.3 Dependência nova

`httpx` instalado via `pip install --user` no ambiente local de teste, e
**adicionado a `requirements.txt`** (seção própria, comentada como
"habilitado só na branch/worktree `research/rest-mcp-estudos`", mesmo
padrão da seção do LangGraph) — não estava listado antes (grep confirmou:
nenhuma lib HTTP async explícita ali, possivelmente vem transitivo de
`google-genai`, não investigado a fundo). Necessário pra valer porque o
`rest_lab` agora roda dentro do worker Celery real (ver 10.6), não só no
ambiente local de teste — sem isso o container do worker quebraria no
primeiro `import httpx`.

### 10.4 Fora de escopo desta rodada (revisado em 10.6 — usuário pediu integração com WhatsApp na mesma sessão)

- Sem integração com `src/` além do ponto mínimo de entrada em
  `dispatcher_langgraph.py` (ver 10.6) — nenhuma outra parte do núcleo
  tocada.
- Sem LLM decidindo a tool chamada (ver 10.1) — mantido regex mesmo depois
  do pivot pro WhatsApp.
- MCP — próxima rodada, depois do REST validado no canal real.

### 10.6 Pivot na mesma sessão — usuário pediu pra esquecer CLI, testar direto no WhatsApp

Depois do CLI validado (10.2), o usuário decidiu pular a etapa intermediária
e pediu integração direta com o grupo do WhatsApp ("esquece CLI, o projeto é
pra zap zap" — repetido 2x, sinal de prioridade clara). Mudanças feitas:

- **`rest_lab/router.py`** — todo comando agora exige prefixo `rest ` (ex:
  `rest listar usuários`). Motivo: rodando dentro do fluxo real de mensagens
  do grupo, sem prefixo um comando como "usuário 3" poderia colidir com
  linguagem natural real de um aluno. Duas funções públicas agora:
  `tentar_rotear()` (devolve `None` se a mensagem não começa com "rest" —
  usada pelo dispatcher, fast-path de 1 regex antes de testar os outros 9) e
  `rotear()` (usada só pela CLI, nunca devolve `None`, cai no texto de ajuda).
- **`src/application/runtime/dispatcher_langgraph.py::processar()`** — novo
  passo "-1" no topo da função, ANTES até da checagem de funil HITL pendente
  (`state.next`): chama `tentar_rotear(message)` e, se bater, devolve
  `OSResult` direto (`plan_id="rest_lab"`, `rota="REST_LAB"`), sem tocar no
  grafo LangGraph nem no Supervisor real. Único ponto do núcleo tocado nesta
  mudança — nenhuma outra lógica de roteamento/RBAC/guardrails alterada,
  `process_message_task.py` continua chamando `dispatcher_langgraph.processar`
  exatamente como já chamava.
- **`docker-compose.yml`** — `./rest_lab:/app/rest_lab` adicionado como
  volume em todos os serviços que já montavam `./langgraph_experiment`
  (`api`, `worker`, `worker_rag`, `worker_synthesis`, `worker_media`,
  `beat`) — confirmado via parse do YAML (`x-worker-base` propaga pra todos
  os workers). Sem isso o container rodaria uma cópia congelada da imagem,
  mesmo problema já documentado pro `langgraph_experiment/` no `.claude.md`.
- **`requirements.txt`** — `httpx` promovido de dependência só-local pra
  entrada real no arquivo (ver 10.3), porque agora o worker Celery de
  produção (dentro do Docker) importa `rest_lab.router`, que importa
  `rest_lab.tools`, que importa `httpx`.

CLI (`run_test.py`) continua funcionando, ajustado pro mesmo prefixo `rest `
(reteste manual OK após a mudança: `rest listar usuários`, `rest usuário 3`,
`rest ajuda`, e uma mensagem sem prefixo confirmando que não intercepta).

**Não testado ainda nesta sessão**: o caminho real dentro do Docker/WhatsApp
— o ambiente onde este trabalho foi feito não tem o daemon Docker acessível
(`docker ps` falhou: "no such file or directory" no socket), então só foi
possível validar sintaticamente (`py_compile`) e via YAML parse. Teste real
end-to-end (mandar "rest listar usuários" no grupo do WhatsApp) fica
pendente pro usuário rodar no ambiente com Docker.

### 10.7 Pendências pra próxima sessão

1. **Testar de verdade no WhatsApp** (grupo homologado, `ALLOWED_GROUP_ID`)
   — rebuildar/subir os containers (`docker compose --profile core --profile
   monitoring --profile app --profile gateway up -d --build`, já que
   `requirements.txt` mudou — só `--force-recreate` não pega dependência
   nova, precisa rebuild de imagem) e mandar `rest listar usuários`,
   `rest usuário 3`, `rest listar produtos` no grupo. Prestar atenção
   especial no tamanho da mensagem de `listar_usuarios`/`listar_produtos`
   (preocupação original do usuário) — se o WhatsApp cortar/formatar mal,
   ajustar `_LIMITE_LISTA` em `rest_lab/tools.py`.
2. Decidir se avança pra function-calling real (Gemini nativo) nesta mesma
   pasta ou se mantém regex — depende do que o usuário quer demonstrar
   (regex prova "integração REST funcional"; function-calling prova
   "agente decidindo sozinho", gap real do projeto identificado em 10.1).
3. Começar o estudo de MCP (servidores de referência oficiais — Everything,
   Fetch, Filesystem, Git — e GitHub MCP server/DeepWiki como análogos a
   integração real tipo GLPI/SIGAA), conforme lista já validada em sessão
   anterior.

## 11. Sessão 2026-08-12 — Roadmap MCP & Multimodal (auditoria + pesquisa + Sprint 1.1)

Pedido do usuário: evoluir o Oráculo com capacidades multimodais (STT, TTS,
Vision, geração de imagem) e MCPs externos, mas só depois de auditar o
projeto e pesquisar tecnologias atuais, com plano em fases/sprints aprovado
antes de qualquer implementação.

**Fase 0 (auditoria + pesquisa)**, feita via 6 agentes (3 de auditoria de
código, 3 de pesquisa com fontes primárias — HuggingFace, repos oficiais).
Achados principais:

- Oráculo já tem STT (`AudioService.transcribe()` via Gemini 2.5 Flash áudio
  nativo, `src/infrastructure/services/audio_service.py`) e TTS
  (`AudioService.synthesize()` via gTTS) implementados, mas **órfãos** —
  nenhum worker/rota real os aciona hoje.
- Não existe nenhum serviço de Vision. Bytes de imagem/áudio recebidos do
  usuário nunca são baixados no fluxo de chat normal (`webhook_controller.py`
  já popula `has_media`/`media_type`, mas `router/supervisor.py` nunca lê).
- Hardware é CPU-only (torch CPU-only no Dockerfile, sem CUDA) — isso
  descartou geração de imagem local (FLUX/SDXL, minutos por imagem em CPU
  e/ou licença não-comercial) e favoreceu manter Vision/STT via Gemini cloud
  (já integrado, segundos de latência) em vez de VLM local (10s-2min em CPU).
  TTS foi a única capability onde vale trocar o baseline: gTTS → Piper (MIT,
  tempo real em CPU) ou Kokoro (Apache-2.0).
- MCP: só `mcp_lab`/`rest_lab` (roteamento regex, não LLM) tocam produção
  hoje, confirmando o que já estava documentado no `.claude.md`. Nenhum MCP
  novo é bloqueante para o trabalho multimodal — GitHub MCP oficial é o
  único 🟢 "pronto pra produção" achado na pesquisa; o resto é 🟡/🔴.

Usuário decidiu (via pergunta direta): **adiar completamente geração de
imagem** — não entra em nenhuma fase, documentado como decisão consciente,
não esquecimento.

Plano completo (8 fases, sprints, riscos, segurança, testes, observabilidade)
escrito em `C:\Users\User\.claude\plans\claude-md-arquitetura-oraculo-md-soft-moonbeam.md`.
Usuário aprovou pedindo um adicional: monitoramento **ajustável** via
Prometheus/Grafana (stack que ele já usa) — adicionado como Sprint 1.3 e
seção dedicada no plano, replicando o padrão de métricas SIGAA já existente
em `src/infrastructure/observability/metrics.py` (histogram de latência +
counters de sucesso/falha, agora com label `provider` em tudo, para que
trocar `STT_PROVIDER`/`TTS_PROVIDER`/`VISION_PROVIDER` no `.env` não exija
nenhuma mudança de código nas métricas/dashboards).

**Sprint 1.1 implementada nesta mesma sessão** (Fundação de Providers):

- `src/domain/ports/speech_to_text_provider.py` (`ISpeechToTextProvider` +
  `TranscriptionResult`) e `text_to_speech_provider.py`
  (`ITextToSpeechProvider` + `SynthesisResult`) — Protocols espelhando
  `ILLMProvider` (`src/domain/ports/llm_Provider.py`).
- `src/infrastructure/adapters/gemini_stt_provider.py` (`GeminiSTTProvider`)
  e `gtts_provider.py` (`GTTSProvider`) — lógica portada de `AudioService`
  (que continua intocado nesta sprint — a religação por factory/settings é
  Sprint 1.2). Ao portar, um `base64.b64encode()` morto (calculado e nunca
  usado) foi removido do código original do STT.
- 8 testes unitários novos (`tests/unit/infrastructure/adapters/`), mocks de
  `google.genai.Client`/`gtts.gTTS`, todos passando.
- Suite completa (`tests/unit`, 200 testes) rodada para checar regressão:
  186 passed, **14 failed — todas pré-existentes, em arquivos não tocados
  por esta sprint** (`test_dispatcher.py` SIGAA, `test_langgraph_crud_hitl.py`,
  `test_langgraph_ticket_hitl.py`, `test_registration_repository.py`) —
  confirmado via `git status` que só arquivos novos foram criados, nenhum
  existente foi modificado. Não investigadas/corrigidas — fora do escopo
  desta sprint, registrar aqui para não confundir com regressão futura.

**Sprint 1.2 aprovada e implementada na sequência, mesma sessão** (religar
`AudioService` via factory/config, conforme o plano):

- `src/infrastructure/settings.py` ganhou `STT_PROVIDER="gemini"` e
  `TTS_PROVIDER="gtts"` (bloco `── Multimodal (STT/TTS/Vision) ──`, logo
  após o bloco `GEMINI_*`).
- `src/infrastructure/adapters/stt_factory.py`/`tts_factory.py` — cada um
  com uma função `get_x_provider(provider_name: str | None = None)` que lê
  `settings.X_PROVIDER` (ou aceita override explícito, case-insensitive) e
  devolve a instância singleton certa; nome desconhecido levanta
  `ValueError` claro em vez de falhar silencioso.
- `src/infrastructure/services/audio_service.py` reescrito para delegar
  `transcribe()`/`synthesize()` para o provider resolvido pela factory —
  `AudioResult` (contrato externo consumido pelos workers) ficou idêntico,
  só a implementação interna mudou. Import morto (`base64`, `tempfile`,
  lógica duplicada de gTTS) removido — essa lógica já vive só em
  `gtts_provider.py`/`gemini_stt_provider.py` desde a Sprint 1.1.
- 10 testes novos (`test_stt_factory.py`, `test_tts_factory.py`,
  `test_audio_service.py`) cobrindo seleção por settings, override
  explícito, erro em provider desconhecido, e delegação real do
  `AudioService` (sucesso e propagação de falha).
- Suite completa rodada de novo: **196 passed, 14 failed — exatamente os
  mesmos 14 de antes**, nenhuma regressão nova. `worker_audio_to_text.py`/
  `worker_text_to_audio.py` não precisaram de nenhuma alteração — continuam
  chamando `get_audio_service().transcribe()/.synthesize()` normalmente,
  agora batendo nos providers configuráveis por baixo dos panos.

Trocar provider hoje (`STT_PROVIDER=gemini`/`TTS_PROVIDER=gtts`, únicas
opções implementadas até aqui) é só config — o código já está pronto para
receber um segundo provider de cada tipo (ex.: Piper na Sprint 3) sem tocar
em `AudioService` de novo, só adicionando um `elif` na factory.

**Sprint 1.3 + Fase 2 (STT no fluxo real) — pedidas juntas pelo usuário na
mesma sessão** ("sprint 1.3 e faça logo a ligando stt pro fluxo de verdade,
to querendo dar docker compose -d --build logo"):

- **Sprint 1.3 (observabilidade)**: `src/infrastructure/observability/metrics.py`
  ganhou o grupo Multimodal (mesmo padrão das métricas SIGAA já existentes) —
  `oraculo_stt_latency_ms`/`oraculo_stt_requests_total`,
  `oraculo_tts_latency_ms`/`oraculo_tts_requests_total`,
  `oraculo_vision_latency_ms`/`oraculo_vision_requests_total`,
  `oraculo_vision_confidence_last`, todas com label `provider`. 4 alertas
  novos em `observability/alert_rules.yml` (`HighSTTFailureRate`,
  `HighTTSFailureRate`, `HighVisionFailureRate`, `HighVisionLatency`) com
  limiares editáveis direto no YAML — isso é o "ajustável" que o usuário
  pediu. **Achado importante**: não existe dashboard Grafana versionado no
  repo (`observability/grafana/provisioning/dashboards/` só tem o YAML de
  provisioning, os painéis reais vivem no volume Docker `grafana_data`,
  criados manualmente na UI) — não dá pra "adicionar uma linha Multimodal"
  em um JSON que não existe no repo; documentado como pendência do usuário
  (queries PromQL prontas: `oraculo_stt_latency_ms_bucket`,
  `rate(oraculo_stt_requests_total[5m])`, etc., filtráveis por `provider`).

- **Fase 2 (ligar STT no fluxo real)**:
  - `process_message_task.py::_handle_message()` — `user_context` ganhou
    `msg_key_id` (faltava; `has_media`/`media_type` já eram propagados mas
    `msg_key_id` não, e sem ele não dá pra baixar o áudio de verdade via
    Evolution API).
  - `dispatcher.py::processar()` ganhou um Fast-Path `-1` (roda ANTES de
    guardrails/HITL/orchestrator, porque nota de voz chega com `message`
    vazio): se `media_type == "audioMessage"` e há `msg_key_id`, chama
    `_transcrever_audio_recebido()` — baixa o áudio via
    `EvolutionAdapter.baixar_midia_base64()`, cap de 16MB (mesmo número de
    `_MAX_ENVIO_MB` em `worker_media_download.py`), despacha
    `worker_audio_to_text` (queue=`media`, já existia, estava órfão) via
    `WorkerRegistry.dispatch()` e faz polling em `plan:results:{plan_id}:{step_id}`
    (mesmo Redis que o worker já escreve, reaproveitando
    `redis_state.get_result_cache()` que já existia sem consumidor
    síncrono). Timeout de 20s. Se falhar, responde com mensagem amigável
    (`rota="AUDIO_TRANSCRIBE"`) em vez de vazar erro técnico. Métricas via
    `get_metrics().observe_stt(provider, ms, sucesso)`.
  - Decisão de design: dispatch via Celery pro worker `media` (não chamada
    inline no worker `default`) — `CELERY_CONCURRENCY=1` no `.env` (ver
    nota de 2026-08-02 acima) faz o worker `default` processar mensagens
    serialmente; transcrever inline bloquearia TODOS os usuários enquanto
    isso rodasse. Rodar no worker `media` mantém o `default` livre.
  - 12 testes novos (`test_metrics_multimodal.py`, `test_dispatcher_stt_fastpath.py`).

- **Bug real pré-existente encontrado E corrigido nesta rodada** (exposto
  pelos testes novos, não introduzido por eles): `src/router/supervisor.py`
  registrava `Histogram("oraculo_router_latency_ms", ...)` e
  `Counter("oraculo_router_cache_hit_total", ...)` **direto via construtor**,
  sem a proteção `_get_or_create` que `PrometheusMetrics`
  (`infrastructure/observability/metrics.py`) usa — e os DOIS módulos
  registram uma métrica com o MESMO NOME LITERAL. Enquanto `router.supervisor`
  sempre importava antes de qualquer `get_metrics()` rodar, o `_get_or_create`
  do lado de `metrics.py` simplesmente reaproveitava o coletor já existente
  (silencioso, sem erro). O Fast-Path de STT novo chama `get_metrics()` bem
  cedo (antes do 1º `rotear()` de uma sessão) — inverteu a ordem em cenários
  onde `router.supervisor` ainda não tinha sido importado, e a segunda
  tentativa de registro (a insegura, em supervisor.py) explodia com
  `ValueError: Duplicated timeseries in CollectorRegistry`. Fix: `supervisor.py`
  ganhou o mesmo padrão de registro seguro (`_get_or_create_metric()`, cópia
  local da lógica de `_get_or_create` pra não acoplar a um símbolo privado de
  outro módulo). **Isso era uma bomba-relógio de produção também**, não só
  de teste — dependia de qual módulo importava primeiro em cada processo
  Celery, não só nos testes que expuseram o bug agora.
  - Suite completa: **206 passed, mesmas 14 falhas pré-existentes de sempre**
    (as mesmas de sempre, nenhuma nova).

**Pendência explícita pro usuário**: `worker` (fila default, roda
`dispatcher.py`/roteamento) E `worker_media` (fila media, roda
`worker_audio_to_text`) os DOIS precisam estar rodando a imagem nova depois
do `docker compose up -d --build` — reiniciar só um deles deixa metade do
fluxo rodando código velho, silenciosamente (mesma lição já documentada
acima sobre múltiplos containers de worker). Nenhuma dependência nova foi
adicionada ao `requirements.txt` nesta rodada (Gemini/gTTS/prometheus_client
já estavam instalados) — `--build` não é estritamente necessário só por
causa deste código (o volume mount de `./src` já bastaria + restart), mas
não atrapalha.

**Bug real de produção encontrado E corrigido na hora, mesma sessão** —
usuário testou com uma nota de voz real logo depois do deploy e mandou o log:
`langchain_google_genai._common.GoogleGenerativeAIError: Error embedding
content (INVALID_ARGUMENT): 400 ... 'EmbedContentRequest.content contains an
empty Part'`, capturado silenciosamente por `SemanticCache.get()` (try/except
que loga warning e segue, por isso a task ainda "succeeded" e mandou uma
resposta — só que sem transcrever o áudio de verdade). Causa raiz: o
Fast-Path de STT só tinha sido colocado em `dispatcher.py::processar()`, mas
o entry point REAL chamado por `process_message_task.py::_handle_message()`
é `dispatcher_langgraph.py::processar()` — que só delega pro `dispatcher.py`
original quando a rota classificada NÃO é uma das que ele mesmo trata
(TICKET_ABERTURA/CRUD/RAG). Nota de voz sem legenda → `rotear("", ...)` →
rota `GERAL` (uma rota RAG, tratada DIRETO pelo LangGraph) → nunca passava
por `dispatcher.py` → `message=""` ia direto pro node de RAG do LangGraph,
que embedava a query vazia. **Lição pra qualquer fast-path novo que precise
rodar antes da classificação de rota**: sempre checar `dispatcher_langgraph.py`
além de `dispatcher.py` — são DOIS entry points, não um só, e o LangGraph só
delega pro dispatcher.py original pras rotas que ele mesmo não trata.

Fix: mesma interceptação de STT (`_transcrever_audio_recebido`, reaproveitada
de `dispatcher.py` — não duplicada) adicionada em
`dispatcher_langgraph.py::processar()`, rodando ANTES até dos labs REST/MCP
(passo "-2"). Depois de transcrever com sucesso, `user_context` é substituído
por uma cópia com `media_type`/`msg_key_id` zerados antes de qualquer
delegação pro `dispatcher.py` original — evita baixar/transcrever o MESMO
áudio duas vezes no caminho de delegação (ex.: rota SIGAA). A interceptação
em `dispatcher.py` foi MANTIDA (não removida) como rede de segurança: outros
consumidores chamam `dispatcher.py::processar()` direto (admin hub/eval_api,
ver docstring do módulo), e o LangGraph está "em reavaliação, não aprovado
pra main" — se o import em `process_message_task.py` for revertido pro
`dispatcher.py` puro, essa rede de segurança vira o único caminho ativo.
3 testes novos (`test_dispatcher_langgraph_stt.py`) cobrindo: transcrição
acontece antes de `rotear()`/`_get_graph()`; falha de transcrição retorna
erro amigável sem chamar o grafo; delegação pro dispatcher.py original
recebe `user_context` já limpo. **Achado lateral**: pacote `mcp` não estava
instalado no venv local (só dentro do Docker) — instalado agora
(`pip install mcp` no `./venv`) pra rodar os testes que importam
`dispatcher_langgraph.py` (que sempre tenta `mcp_lab.router` antes de
qualquer outra coisa) sem precisar mockar todo o import chain.
Suite completa: 209 passed, mesmas 14 falhas pré-existentes de sempre.

**Dois achados adicionais, mesma sessão, usuário testando ao vivo com imagem e comando de vídeo:**

1. **"baixe vídeo de `<termo>`" não baixava** — `_RE_YTB_BUSCA` (`router/supervisor.py`) só reconhecia o verbo "buscar", não "baixar/baixe". O orquestrador LLM (`router/llm_fallback.py`) já classificava a intenção certa (`call_media`, prompt já cobre "usuário pede para baixar um vídeo"), mas a regex de EXTRAÇÃO DE TERMO (usada tanto no Layer 1 do Supervisor quanto no Fast-Path de `dispatcher.py`) não reconhecia essa frase — a mensagem inteira virava "url" e o yt-dlp falhava com "not a valid URL" (mesmo bug de fundo já documentado em sessão anterior, só que pra uma frase diferente). Fix: regex ampliada pra `(?:buscar|procurar|baixar|baixe)`. Testado: não colide com "tem vídeo sobre isso?" (pergunta acadêmica real).

2. **Imagem sem legenda tinha o MESMO bug do áudio, sem tratamento nenhum** — usuário mandou imagem de teste, `message=""` chegava até o RAG/embeddings (Vision ainda não existe, Fase 4/5 do roadmap). Fix: novo guard "-1b"/"-1c" (mesmo padrão dos fast-paths de áudio, em AMBOS os entry points — `dispatcher.py` e `dispatcher_langgraph.py`) — se `message` vazio E `has_media=True` (qualquer tipo que não seja áudio, já tratado antes), responde com mensagem amigável (`rota="UNSUPPORTED_MEDIA"`) em vez de deixar vazar pro RAG. Não é Vision de verdade (isso continua Fase 4/5) — só evita a falha silenciosa/`embed_query("")`.

5 testes novos (`test_regex_rapido_media_download_busca_por_termo`,
`test_processar_com_imagem_sem_legenda_retorna_mensagem_amigavel`,
`test_imagem_sem_legenda_retorna_mensagem_amigavel_sem_chamar_grafo`, e mais 2
cobrindo os dois entry points). Suite completa: 212 passed, mesmas 14 falhas
de sempre.

## 12. Sessão 2026-08-12 (continuação) — Fase 3: TTS no fluxo real (Kokoro)

Usuário pediu pra seguir pra Fase 3 do roadmap. Antes de implementar, achado
importante que mudou a recomendação original da pesquisa:

**Licença do Piper mudou desde a pesquisa da Fase 0** — `rhasspy/piper`
(MIT) foi **arquivado em 10/2025**; o sucessor mantido é
`OHF-Voice/piper1-gpl`, e o pacote `piper-tts` no PyPI é **GPL-3.0-or-later**
a partir da v1.4.0 (jan/2026) — só a v1.3.0 (jul/2025) e anteriores ainda são
MIT, mas essa versão é congelada/sem manutenção. Achado confirmado via
WebFetch direto no GitHub/PyPI (não confiado de memória, achado real que
contradiz a pesquisa original da Fase 0 — o mercado mudou entre a pesquisa e
a implementação). Perguntado ao usuário como tratar; decisão: **trocar pra
Kokoro-82M** (Apache-2.0, sem ambiguidade de licença) como único provider de
TTS local — Piper descartado inteiramente pra este projeto.

**Kokoro testado localmente antes de escrever qualquer código** (mesma
disciplina da Sprint 1.1): `pip install kokoro soundfile` no venv, síntese
real de "Estou com um erro ao tentar acessar o sistema da universidade."
funcionou — 2.2s pra gerar ~3.9s de áudio (tempo real+ em CPU comum). Achados
que mudaram a implementação:
- **Não precisa de torch extra nem de `espeak-ng` via apt** — `kokoro`
  reaproveita o torch já instalado (CrossEncoder) e `espeakng-loader`
  (dependência transitiva) já embute os binários do espeak-ng via pip. Menos
  mudança de infra do que a pesquisa original antecipava.
- `KPipeline(lang_code='p')` + `pipeline(texto, voice='pf_dora')` — 3 vozes
  pt-BR disponíveis: `pf_dora` (fem., escolhida como padrão), `pm_alex`/
  `pm_santa` (masc.).
- Retorna `torch.Tensor`, não `numpy.array` direto — precisa `.detach().cpu().numpy()`
  antes de `soundfile.write()` (descoberto no teste local, não documentado
  claramente na doc oficial).
- Conflito de versão `click` (gtts exige `<8.2`, kokoro/spacy puxam `8.4.2`)
  — testado que **não quebra** o gTTS na prática (síntese real via
  `GTTSProvider` continua funcionando, warning do pip é inofensivo aqui).

**Implementado:**
- `src/infrastructure/adapters/kokoro_tts_provider.py` (`KokoroTTSProvider`)
  — pipeline carregado lazy (~15s na 1ª síntese por processo, fica em
  memória depois), `synthesize()` roda em `asyncio.to_thread` (CPU-bound).
  Texto truncado em 500 chars (mesmo cap do `GTTSProvider`, mesma limitação
  conhecida).
- `tts_factory.py` ganhou a opção `"kokoro"`. `settings.TTS_PROVIDER` mudou
  o default de `"gtts"` pra `"kokoro"` (esse é o objetivo da Fase 3); nova
  `settings.KOKORO_VOICE = "pf_dora"`.
- `Dockerfile` — novo `RUN` logo depois do CrossEncoder, baixando o modelo
  base + a voz `pf_dora` em build-time (síntese real de teste, não só
  instanciar o pipeline — força o download da voz também). **Não testado em
  build Docker de verdade nesta sessão** (sem daemon Docker acessível aqui)
  — só validado localmente fora do container. Primeira validação real fica
  pro `docker compose up -d --build` do usuário.
- `requirements.txt` — `kokoro>=0.9.2`, `soundfile>=0.14.0`.

**Sprint 3.2 (gatilho de uso) — decisão de design**: opt-in explícito via
frase no texto digitado (`_RE_AUDIO_REPLY`/`_quer_resposta_em_audio()` em
`dispatcher.py`, mesmo padrão de módulo compartilhado de `_transcrever_audio_recebido`),
não automático em toda resposta (TTS ainda tem custo de cold-load ~15s/
processo, nem toda resposta faz sentido em voz). Reconhece "em/por/de áudio",
"manda(r/e/em) (um/uma mensagem de) áudio" — testado contra falsos positivos
("o áudio que mandei não carregou" não dispara). **Limitação conhecida e
documentada**: só detecta o pedido no texto ORIGINAL digitado/legenda — se o
pedido for FALADO dentro da própria nota de voz ("responda em áudio" dito em
voz), não é capturado, porque a checagem roda sobre o texto bruto recebido
por `_handle_message()`, não sobre o transcript (que só existe depois, dentro
de `dispatcher_langgraph.py`). Cobre o caso principal (pedido digitado).

**Sprint 3.3 (envio de saída)**: `process_message_task.py::_enviar_resposta_em_audio()`
— sintetiza `result.answer` (sem o sufixo "_Avalie: !1 a !5_", que não faz
sentido em voz) via `AudioService.synthesize()` e envia com
`EvolutionAdapter.enviar_midia_base64(mediatype="audio", mimetype="audio/wav")`
— mesmo endpoint/padrão já usado pra vídeo do YouTube, arquivo temporário
sempre apagado depois (sucesso ou falha, `finally`). Só ligado no branch LLM
de `_handle_message()` (grupo homologado) — mesmo escopo dos fixes de STT
desta sessão.

21 testes novos no total (`test_kokoro_tts_provider.py`,
`test_tts_factory.py` +1, `test_audio_reply_trigger.py`,
`test_enviar_resposta_em_audio.py`). Suite completa: **233 passed, mesmas 14
falhas pré-existentes de sempre**.

**Pendências explícitas pro usuário testar de verdade**:
1. `docker compose up -d --build` — a camada nova do Kokoro no Dockerfile
   nunca rodou de verdade (baixa ~300MB+ do HF Hub na 1ª build, aumenta o
   tempo/tamanho da imagem — não medido).
2. Testar qualidade de voz real do `pf_dora` em pt-BR (só validado que
   gera áudio, não a qualidade/naturalidade percebida).
3. Testar o gatilho de verdade no WhatsApp: escrever "explica X em áudio" e
   confirmar que chega uma segunda mensagem (áudio) além do texto.
4. `worker` (roda `_handle_message`) e `worker_media` (não usado por TTS
   desta vez — a síntese roda inline no worker `default`, diferente do STT
   que usa Celery separado; ver nota abaixo) precisam da imagem nova.

**Nota de arquitetura — TTS não usa Celery/fila separada como o STT usa**:
decisão deliberada por simplicidade nesta sprint — `_enviar_resposta_em_audio()`
roda no MESMO worker `default` que processa a mensagem, não despacha pra
`worker_text_to_audio` (que continua existindo mas órfão, só chamado via
`registry.dispatch()` manual). Diferente do STT (que despacha pro worker
`media` justamente pra não travar o `default` com `CELERY_CONCURRENCY=1`),
aqui o cold-load de ~15s do Kokoro SÓ acontece na 1ª síntese por processo —
depois fica quente. Se isso se mostrar um problema real de latência
(bloqueando outras mensagens) depois de testar em produção, mover pra
`worker_media` como o STT é o próximo passo natural — não fiz agora pra não
adicionar complexidade (round-trip Celery+polling) sem evidência de que é
necessário.

**A "evidência de que é necessário" apareceu na hora — usuário testou e o
worker `default` MORREU (OOM-kill).** Log real:

```
🔊 [KOKORO] Carregando pipeline (lang_code='p')...
WARNING: Defaulting repo_id to hexgrad/Kokoro-82M...
HTTP Request: HEAD https://huggingface.co/.../config.json
... (baixando kokoro-v1_0.pth)
ERROR/MainProcess: Process 'ForkPoolWorker-2' pid:218 exited with 'signal 9 (SIGKILL)'
billiard.exceptions.WorkerLostError: Worker exited prematurely: signal 9 (SIGKILL) Job: 4.
```

**Causa raiz dupla, achada lendo o próprio `docker-compose.yml`:**

1. `worker` (fila default, onde `_handle_message()` roda) tem `mem_limit: 768m`
   e um comentário já existente no arquivo avisando que esse container
   "concentra 3 fontes de pressão de memória" (Playwright/Chromium do SIGAA,
   event loop do LangGraph, parsing de PDF) — rodar Kokoro (torch + spacy +
   curated-transformers + checkpoint de 82M params) INLINE nesse mesmo
   processo, ainda por cima, foi a decisão errada da Sprint 3.3 original.
   O SIGKILL matou o container inteiro — **derrubando TODO o processamento
   de mensagens**, não só a resposta em áudio (`WorkerLostError` também
   perde a task em andamento).
2. O log mostra download acontecendo em RUNTIME (`HTTP Request: HEAD
   https://huggingface.co/...`), não carregando de um cache já baked —
   confirma que o container rodando ainda não tinha a camada nova do
   Dockerfile (imagem não rebuildada, ou rebuild não pegou por algum motivo
   não investigado nesta sessão).

**Fix aplicado imediatamente:**

- `process_message_task.py::_enviar_resposta_em_audio()` reescrita — não
  chama mais `AudioService.synthesize()` inline. Agora despacha
  `worker_text_to_audio` via Celery (`WorkerRegistry.dispatch`, fila
  `media`, mesmo padrão do STT) e faz polling em
  `plan:results:{plan_id}:{step_id}` (timeout 25s — cold-load do Kokoro
  ~15s + fila + polling).
- **Segundo bug real encontrado no processo** (pré-existente, nunca tinha
  sido exercitado de ponta a ponta antes): `worker_text_to_audio.py`
  deixava o arquivo `/tmp` "pra quem consumir apagar" — mas quem despacha
  esse worker roda num CONTAINER DIFERENTE (`worker` vs `worker_media`),
  sem acesso nenhum a esse caminho local. E pior: `_salvar()` filtrava
  explicitamente `audio_b64` antes de gravar no Redis
  (`{k:v for k,v ... if k != "audio_b64"}`) — não tinha NENHUM jeito de o
  áudio chegar no consumidor, arquivo inacessível E bytes não persistidos.
  Fix: o próprio worker apaga o arquivo depois de ler (é quem criou, é quem
  deve limpar) e `audio_b64` passa a ser persistido no Redis (TTL 120s, sem
  filtro) — payload pequeno o bastante (texto já truncado em 500 chars →
  WAV curto) pra não ser um problema de tamanho de chave.

**Risco residual, não testado, deixado explícito pro usuário**: `worker_media`
(768m, sem Playwright competindo, `--pool=solo` então só 1 task por vez
nesse container) é mais seguro que o `worker` default, mas 768MB ainda pode
ser pouco pra torch+spacy+curated-transformers+checkpoint de 82M todos
residentes ao mesmo tempo — não medido, spike transiente durante
deserialização do checkpoint pode passar do resident final. Se OOM
acontecer de novo (agora em `worker_media`), o fix é subir `mem_limit` desse
serviço em `docker-compose.yml` — não fiz essa mudança sozinho porque é uma
decisão de orçamento de RAM do host inteiro que não me cabe decidir sem o
usuário saber quanto de RAM total a máquina tem disponível.

10 testes novos/reescritos (`test_enviar_resposta_em_audio.py` reescrito do
zero pro novo mecanismo de dispatch, `test_worker_text_to_audio.py` novo).
Suite completa: **235 passed, mesmas 14 falhas pré-existentes de sempre**.

**Terceiro bug real de produção, mesma sessão, achado testando depois do
rebuild**: com o OOM corrigido, o pedido de áudio parou de derrubar o
worker — mas voltou uma resposta em TEXTO tipo "Não consigo te explicar
sobre o Office 365 em áudio, pois sou um assistente...". Causa: a frase
inteira do usuário ("Me explique em áudio sobre o Office 365") ia direto
pro LLM de síntese/RAG como a pergunta — o modelo via literalmente "em
áudio" na pergunta e respondia SOBRE o pedido de formato (recusando, porque
"é um assistente de texto"), em vez de responder a pergunta de verdade
sobre Office 365. A frase-gatilho era só um sinal pro MEU roteamento de
entrega (`_quer_resposta_em_audio`), nunca deveria ter ido pro LLM como
parte da pergunta.

Fix: nova `_remover_pedido_audio()` em `dispatcher.py` (mesmo módulo de
`_quer_resposta_em_audio`) — remove a frase-gatilho do texto ANTES de virar
`message` pro RAG/orchestrator/synthesis, com fallback pro texto original se
a limpeza deixar a mensagem vazia (raro: mensagem que É só o gatilho, tipo
"em áudio" sozinho). `process_message_task.py::_handle_message()`
reestruturado: `quer_audio`/`mensagem` (limpa) calculados uma vez, ANTES de
`cognitive_processar()` — a versão limpa vai pro RAG/memória, a decisão de
mandar áudio usa o texto original (já detectado antes da limpeza).

4 testes novos em `test_audio_reply_trigger.py`. Suite completa: **239
passed, mesmas 14 falhas de sempre**.

**Padrão que se repetiu 3x nesta sessão, vale reter**: toda vez que um
recurso novo lê o TEXTO da mensagem do usuário pra tomar uma decisão de
roteamento/entrega (STT: media_type; TTS: frase-gatilho), o sinal usado pra
decisão vazava pro conteúdo que o LLM via — cada vez descoberto só no teste
real, não em teste unitário isolado. Lição pra qualquer gatilho textual
futuro (ex.: Vision): sempre separar explicitamente "o que decide o
roteamento" de "o que o LLM recebe como pergunta", e limpar/sanitizar antes
de repassar adiante.

**Quarto bug real, mesma sessão, achado testando de novo depois do fix
acima**: com a pergunta limpa, o texto saiu certo — mas o áudio nunca
chegou. Log mostrou dois problemas distintos:

1. **1ª tentativa: timeout de verdade** (`⚠️ [TTS] Síntese falhou ou deu
   timeout | payload=None`, ~27s desde o despacho) — o cold-load do Kokoro
   em produção (container mais fraco que a máquina de dev onde medi ~15s)
   estourou o timeout de 25s da Sprint 3.3. Fix: `_TTS_TIMEOUT_S` subiu pra
   45s.
2. **2ª tentativa: Evolution aceitou (HTTP 201 `sendMedia`) e o código
   logou "Resposta em áudio enviada" — mas nada chegou no WhatsApp.** Causa:
   `_enviar_resposta_em_audio()` mandava `mimetype="audio/wav"` — Kokoro
   gera WAV cru via `soundfile`, e a Evolution API aparentemente aceita esse
   payload na API síncrona (retorna 201) mas falha silenciosamente na
   entrega de verdade pro WhatsApp (falha assíncrona, não reportada de
   volta no HTTP response). Achado ao conferir o ÚNICO outro envio de áudio
   já existente neste projeto (`worker_media_download.py::_enviar_para_whatsapp()`,
   fallback de áudio do YouTube) — ele usa `mimetype="audio/mpeg"` (MP3),
   não WAV. **Nota**: nem esse outro caminho tinha sido confirmado
   funcionando via WhatsApp real antes desta sessão (só documentado como
   "não testado" no `.claude.md`), então MP3 não era 100% garantido, mas é
   a aposta muito mais segura — WAV cru é mal suportado por gateways de
   mensageria em geral, MP3 é universal.

   Fix: `kokoro_tts_provider.py::_synthesize_sync()` agora codifica o áudio
   pra MP3 via `lameenc` (encoder MP3 puro-Python, wheel `manylinux2014_x86_64`
   pronta pro `python:3.11-slim` da imagem — sem precisar instalar `ffmpeg`
   via apt, que não estava instalado em lugar nenhum do projeto). Testado
   de verdade localmente antes de aplicar: `Encoder().encode(pcm16.tobytes())`
   sobre áudio real gerado pelo Kokoro → arquivo com header `\xff\xf3`
   (frame sync MP3 válido), 31680 bytes pra ~3.9s de áudio a 64kbps (número
   bate). `_enviar_resposta_em_audio()` também corrigido pra
   `mimetype="audio/mpeg"`/`filename="resposta.mp3"`, batendo com o que
   Kokoro E gTTS produzem os dois agora (gTTS já sempre gerou MP3 — o
   mimetype "audio/wav" hardcoded nunca tinha batido com gTTS também, bug
   latente que só apareceu agora que o caminho foi exercitado de verdade
   pela 1ª vez).

`lameenc>=1.8.0` adicionado ao `requirements.txt`. Teste novo em
`test_kokoro_tts_provider.py` confere o header binário real do MP3 gerado
(`0xFF` + top 3 bits `0xE0`), não só mocka a chamada. Suite completa: 239
passed, mesmas 14 falhas de sempre (contagem de teste não mudou — testes
existentes atualizados, não adicionados, pra essa rodada específica).

## 13. Sessão 2026-08-15 — Multi-provider LLM (Gemini/DeepSeek/Groq), monitoramento real de custo, CI/CD, limpeza

> Contexto: preocupação do usuário era puramente financeira — "não quero um
> projeto de agente que custe 10 mil por mês". Trilha da conversa: pesquisa
> própria no Grok sobre preço de mercado → dois documentos novos avaliando
> essa pesquisa contra a arquitetura e o custo REAIS do Oráculo
> (`pesquisa_arquitetura_producao.md`, `analise_custo_real_llm.md`) → decisão
> de agir: multi-provider + monitoramento de verdade. Resumo funde as duas
> pontas (documentos de análise + implementação real feita depois).

### 13.1 Os dois documentos de análise (antes de qualquer código)

- **`pesquisa_arquitetura_producao.md`**: cruza uma pesquisa profunda do
  Perplexity sobre arquitetura de produção (agentes, CI/CD, observabilidade,
  MCP, governança) contra o estado real do repo. Achados: RAG híbrido/
  semantic cache/arquitetura 3 camadas já validam a pesquisa (não precisa
  reconstruir); CI/CD, observabilidade LLM dedicada e governança formal
  **não existiam** (a pesquisa assumia que sim). Roadmap em 6 fases proposto.
- **`analise_custo_real_llm.md`**: avalia uma pesquisa do Grok sobre preços
  de mercado (caso real UFVJM/SERPRO ~R$105mil/ano, ranking de provedores)
  contra o fluxo real de chamadas do Oráculo. Achado central: uma mensagem
  RAG dispara até 6 chamadas Gemini (não 1, como a pesquisa genérica
  assumia), e **não existia telemetria persistente nenhuma de custo** —
  `metricas_llm` (Postgres) existia desde a migration `001` e nunca era
  usada; o único registro real (`registrar_tokens_redis`) tinha TTL de 1h,
  só pro simulador de avaliação do `/hub`.

### 13.2 Decisão explícita de escopo (a pedido do usuário)

Governança formal e a fusão dos classificadores Orquestrador×Supervisor
(`notas.md` §1/§5.1) ficaram **de fora** — "pararemos e conversaremos
depois". Implementado só telemetria de observação sobre o conflito
(contador `oraculo_router_override_total`), não a resolução dele.

### 13.3 O que foi implementado

- **Multi-provider (Gemini/DeepSeek/Groq)**: `infrastructure/adapters/
  llm_factory.py::get_llm_provider()` — ponto único de resolução, troca em
  runtime via Redis (`admin:llm_provider`, editável em `/hub/llm-custo`) ou
  override por agente (`agentes_catalogo.llm_provider`/`llm_model`,
  migration `007`, editável em `/hub/agents`). Novo adapter genérico
  `openai_compatible_provider.py` cobre DeepSeek+Groq com 1 classe só (API
  compatível OpenAI). `groq_provider.py` antigo (LangChain, código morto)
  removido.
- **Migração parcial dos call sites**: dos 9 arquivos que chamavam
  `genai.Client` direto, migrados os 3 de maior volume/custo
  (`llm_fallback.py` classify+orchestrate, `planning.py`, `synthesis.py`).
  **6 continuam não migrados** (`query_transform.py` ×2,
  `memory_summarizer.py`, `calendar_llm_adapter.py`,
  `graph_extractor_service.py`, `beat_nightly_memory.py`) — próximo passo
  natural, mesmo padrão já estabelecido.
- **Achado que corrige uma afirmação errada minha de sessão anterior**: eu
  tinha dito que "model routing small/large já existe implicitamente" — mentira,
  vinha só da tabela em `arquitetura_oraculo.md` §4.3, o código real usa uma
  ÚNICA `settings.GEMINI_MODEL` pra tudo. Lição: não confiar em doc de
  arquitetura sem checar o código (mesma lição de `notas.md` §9.8 sobre
  `message_stream.py`).
- **Telemetria real conectada**: `MonitoredLLMProvider` (mesmo arquivo do
  factory) grava toda chamada em `metricas_llm` (Postgres, finalmente usada)
  + Prometheus (`oraculo_llm_cost_usd_total`/`_tokens_total`/`_calls_total`,
  label `provider`) + `pricing.py` novo corrige a constante de custo
  desatualizada que existia em `synthesis.py`. 4 métricas de observação do
  roteamento (`notas.md` §5.2) também implementadas.
- **Grafana nunca estava conectado**: achado real — `observability/grafana/
  provisioning/` existia no repo mas o `docker-compose.yml` nunca montava
  esse volume no serviço `grafana`. Corrigido. Dashboard novo
  `llm_custo_providers.json` criado (não visto renderizado ainda).
- **HUB como portal**: `/hub/agents` ganhou seletor de provider/modelo por
  agente; `/hub/llm-custo` (página nova) mostra custo real por
  provider/rota e troca o provider global.
- **CI/CD**: `pytest`/`pytest-asyncio` nunca estiveram declarados em
  arquivo nenhum do repo (achado real, por isso nunca existiu CI). Novo
  `requirements-dev.txt` + `pytest.ini` (markers nunca registrados) +
  `.github/workflows/tests.yml` com services Redis+Postgres reais (mesmos
  defaults que `tests/conftest.py` já hardcodava) rodando `alembic upgrade
  head` + `tests/unit` (exceto `test_registration_repository.py`, falha
  pré-existente documentada) + eval do wiki CTIC.
- **Limpeza**: removidos `oracle_chain.bak`, `eval_copy.bak`,
  `eval_dashboard.bak`, `gmail_tool.py` (confirmado zero chamadores antes de
  apagar). `DISABLE_DOCLING` implementado (pendência exata de §8.5).
  `.env.example` criado (não existia — e um bug real no `.gitignore`, a
  negação `!.env.example` vinha ANTES de `.env.*` e por isso nunca
  funcionava, corrigido).

### 13.4 Testado de verdade nesta sessão (sem Docker — só pip local)

`pytest tests/unit`: **240 passed**, 12 falhas confirmadas como precisando
de Postgres/Redis real (mesma classe de falha de sempre, o CI novo resolve
isso) + 1 regressão real que eu causei
(`test_agent_catalog_repository.py`, corrigida na hora — mock não tinha os
campos novos `llm_provider`/`llm_model`). Migration `007` validada via
`alembic.script.ScriptDirectory` (cadeia resolve, head único), nunca rodada
contra Postgres real. **Chamadas reais a DeepSeek/Groq nunca foram
testadas** (sem chave configurada) — só a mecânica HTTP/parsing foi
validada.

### 13.5 Git — commit "nada" nunca tinha sido enviado (achado ao final)

Usuário achava que já tinha enviado pro GitHub — na verdade só existia um
commit LOCAL com mensagem "nada" (36 arquivos, toda a sessão), nunca
pushado. Reescrito (`git commit --amend`, seguro porque ainda não tinha ido
ao remoto) com mensagem descritiva. Push inicial rejeitado — branch
`research/rest-mcp-estudos` no GitHub tinha avançado 2 commits nesse meio
tempo (outra sessão implementando STT/TTS/Multimodal, ver §11/§12 acima).
`git pull --rebase` com conflitos reais em 2 arquivos (`.claude.md`,
`settings.py`) — resolvidos mantendo as duas contribuições (aditivas, sem
contradição real). Suite reconfirmada depois do merge: 240 passed, mesmas
12 falhas de infra, zero regressão nova. Push final bem-sucedido.

### 13.6 Pendente / próximos passos explícitos

1. ~~Deploy real~~ — feito pelo usuário no mesmo dia (2026-08-15), achou 3
   bugs reais de verdade (ver §13.8 abaixo, já corrigidos e pushados,
   commit `13bffc2`). `alembic upgrade head` ainda precisa rodar contra o
   Postgres real do usuário (colunas `provider`/`llm_provider`/`llm_model`
   não existiam no banco dele até o momento do log).
2. Colar `DEEPSEEK_API_KEY`/`GROQ_API_KEY` reais no `.env` (não no HUB
   ainda — `/hub/config` tem UI pra "Salvar Todas" as API keys, mas o
   endpoint que ela chama, `POST /api/admin/system/env`, **não existe no
   backend** — confirmado de novo, hoje esse botão dá 404). Ver plano
   detalhado em §13.9.
3. Migrar os 6 call sites restantes pro `llm_factory`.
4. Ver dashboard Grafana renderizado de verdade pela 1ª vez.
5. Unir telemetria de STT/TTS/Vision (métricas multimodais próprias, §11/§12
   acima) com `metricas_llm`/custo por provider — hoje são dois mundos
   separados.
6. RBAC testado na `main` (ainda bloqueia a decisão do LangGraph, ver
   `.claude.md`).

### 13.7 Confusão do usuário sobre telemetria — esclarecido nesta rodada

Usuário achou o aviso "Dados de metricas_llm (Postgres)" (`/hub/llm-custo`)
confuso e perguntou o que é Prometheus/Grafana/Postgres nesse contexto.
Resposta registrada aqui pra não se perder:

- **Postgres (`metricas_llm`)** — 1 linha por chamada LLM, guardada pra
  sempre (até decidirem limpar). É o "extrato bancário": dá pra perguntar
  "quanto gastei em agosto com DeepSeek na rota SIGAA". `/hub/llm-custo`
  lê DAQUI, direto — não passa pelo Grafana.
- **Prometheus** — contador em memória, "raspado" (scrape) periodicamente,
  bom pra "o que está acontecendo AGORA", não guarda histórico detalhado
  indefinidamente. É o painel do carro, não o extrato bancário.
- **Grafana** — só a TELA que desenha gráficos em cima do Prometheus (e,
  se configurado, outras fontes). Não guarda dado nenhum sozinho.

Por que os dois existem separados: Postgres é auditável/histórico exato;
Prometheus é operacional/tempo-real sem crescer sem limite. O aviso na
página só existe pra deixar claro que aqueles números específicos vêm do
Postgres (não do Grafana), porque as duas fontes têm dados parecidos mas
não idênticos (Prometheus é agregado, Postgres é por-chamada).

### 13.8 Bugs reais achados no primeiro deploy de verdade (2026-08-15, mesmo dia)

Usuário rodou o Docker de verdade pela 1ª vez com o multi-provider — 3
bugs reais apareceram no log, corrigidos no commit `13bffc2`:

1. `INTERVAL ':horas hours'` em `observability_repository.py` — o
   Postgres lia o bind param como texto literal DENTRO das aspas em vez de
   substituir de verdade ("the server expects 0 arguments... 1 was
   passed"). 4 ocorrências: 3 pré-existentes (`get_metricas_dashboard`,
   `get_metricas_por_rota`, a query de NPS semanal — nenhuma delas escrita
   nesta sessão, só nunca tinham sido exercitadas contra Postgres real
   antes) + 1 minha (`get_metricas_por_provider`, copiei o padrão sem
   perceber que já estava quebrado). Fix: `make_interval(hours =>
   :horas)`.
2. `salvar_audit()`: `:detalhes::jsonb` — o parser de bind params do
   SQLAlchemy se confunde com `::` logo depois do nome do parâmetro,
   virava SQL inválido pro asyncpg. Bug pré-existente, só apareceu porque
   o endpoint novo `/hub/llm/provider` foi o primeiro consumidor real
   dessa função. Fix: `CAST(:detalhes AS jsonb)`.
3. `pricing.py` — preços DeepSeek estavam errados (eu tinha usado
   $0.14/$0.28, o oficial é $0.20/$1.20 + cache $0.02, confirmado pelo
   usuário via `api-docs.deepseek.com`). Corrigido. Achado extra: a
   tabela oficial de "Production Models" do Groq não lista mais
   `llama-3.3-70b-versatile` (settings default atual) — só
   `openai/gpt-oss-120b`/`20b`. Adicionados como alternativa, mas **qual
   modelo Groq usar de fato ainda não foi decidido com o usuário**.

**Lição registrada**: nenhum destes 3 bugs (nem os 2 pré-existentes) tinha
sido pego pelos testes, porque `tests/unit` mocka a sessão do banco —
só apareceram no primeiro contato com Postgres real. Reforça o valor do
CI novo (§13.3) rodando contra services de verdade, mas também mostra que
"testes passando" não significa "SQL válido" quando o SQL usa `text()` cru
com sintaxe específica do dialect.

### 13.9 Plano pra próxima conversa (registrado a pedido do usuário, nada implementado ainda)

Usuário pediu explicitamente pra planejar antes de agir — quatro frentes,
nessa ordem de prioridade:

**Fase 1 — `/hub/config` de verdade (hoje é decorativo, botão dá 404)**
- Remover a seção Langfuse do `templates/hub/config.html` (decisão já
  tomada de não usar, `pesquisa_arquitetura_producao.md` §4.5).
- Adicionar seções DeepSeek (API key + model) e Groq (API key + model),
  mesmo padrão visual das seções Gemini/Evolution já existentes
  (`key-row` com `data-env`).
- Implementar o endpoint que falta: `POST /api/admin/system/env` em
  `src/api/routers/admin/admin_api.py` (mesmo router de `/system`,
  `require_admin_jwt`) — grava no `.env` real do servidor. **Trade-off a
  discutir antes de implementar**: escrever segredo em texto puro num
  arquivo via request HTTP é o padrão que a UI já pressupõe (não inventar
  um novo), mas vale registrar que não é o ideal de segurança — decisão
  consciente de manter simples por ora, não redesenhar sem necessidade.

**Fase 2 — Pricing editável sem rebuild**
- `pricing.py` hoje é uma tabela Python hardcoded — qualquer mudança de
  preço exige rebuild da imagem. Usuário quer poder atualizar preço sem
  isso ("essas porras mudam né").
- Proposta: tabela Postgres nova (`llm_pricing`: provider, modelo,
  input_por_1m, output_por_1m, cache_por_1m, atualizado_em,
  atualizado_por), mesmo espírito do catálogo de agentes
  (`agentes_catalogo`). `pricing.py::calcular_custo_usd` passa a checar
  essa tabela primeiro (via cache Redis curto, mesmo padrão do override
  de provider por agente em `llm_factory.py::_override_do_agente`),
  caindo no dicionário Python hardcoded só como seed/fallback. UI de
  edição em `/hub/config` ou `/hub/llm-custo`.

**Fase 3 — Telemetria detalhada (input/output/cache) na página de custo**
- `metricas_llm` já tem `tokens_entrada`/`tokens_saida`/`cache_hit`/
  `cache_layer` separados (schema desde a migration `001`) — só não estão
  todos expostos na UI ainda. Estender `/hub/llm-custo` pra mostrar
  breakdown input vs output (não só total) e taxa de cache_hit por
  provider/rota.
- **Esclarecer pro usuário a diferença entre os DOIS caches que existem**
  (confusão real nesta conversa): cache SEMÂNTICO (Redis, cosine > 0.92,
  já implementado, evita a chamada LLM inteira — é o que `cache_hit_pct`
  mede hoje) vs cache DE PROMPT do provider (desconto de preço quando o
  prefixo repete, ex. DeepSeek `cache_por_1m` — **não implementado em
  nenhum adapter ainda**, é mecanismo diferente).

**Fase 4 — Retomar o roadmap que ficou pra trás**
- Migrar os 6 call sites restantes pro `llm_factory` (ver §13.6 item 3).
- RBAC testado na `main`.
- Unir telemetria multimodal (STT/TTS/Vision) com `metricas_llm`.

## 14. Sessão 2026-08-17 — Fases 1-4 do plano anterior implementadas, aplicadas no stack real, e `/hub/chat` reescrito pro pipeline de produção

Continuação direta do §13.9 — usuário pediu as 4 fases de uma vez ("bora
pra todas"). Diferente das sessões anteriores, desta vez o trabalho foi
aplicado e testado contra o stack Docker real do usuário (não só código +
`pytest` local), o que expôs mais bugs reais — mesmo padrão do §13.8.

### 14.1 Fase 1 — `/hub/config` de verdade
- `templates/hub/config.html`: removida a seção Langfuse (card externo +
  grupo de API keys — nunca foi instalado de fato, confirmado em
  `pesquisa_arquitetura_producao.md` linha 66). Adicionados grupos
  DeepSeek (`DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL`) e Groq
  (`GROQ_API_KEY`/`GROQ_MODEL`, campo livre — modelo Groq "oficial" segue
  em aberto, não travar na UI).
- `src/api/routers/admin/admin_api.py`: implementado `POST
  /api/admin/system/env` (não existia — era o 404 do §13.9), com allowlist
  explícita de chaves e audit log só com a lista de chaves alteradas
  (nunca os valores). `GET /api/admin/system` passou a devolver os campos
  não sensíveis que o JS de `loadCurrentConfig()` já esperava e nunca
  recebia (`gemini_model`, `deepseek_model`, `groq_model`,
  `embedding_provider`, `evolution_url`, `evolution_instance`,
  `redis_url`, `dev_mode`).

### 14.2 Fase 2 — Pricing editável sem rebuild
- Migration `008_llm_pricing.py`: tabela `llm_pricing` (provider, modelo,
  input/output/cache por 1M, auditoria), seed com os mesmos valores que já
  estavam hardcoded em `pricing.py::_PRECOS`.
- `LlmPricing` (models.py) + `LlmPricingRepository` (novo), mesmo padrão
  de `AgenteCatalogo`/`AgentCatalogRepository`.
- `pricing.py::calcular_custo_usd` passou a checar primeiro uma chave
  Redis `pricing:{provider}:{modelo}` (write-through, sem TTL — mesmo
  padrão do override de LLM por agente em `llm_factory.py`), caindo no
  dicionário hardcoded só como fallback.
- Endpoints `GET/POST /hub/llm-pricing(/data)` em `hub.py` (Postgres +
  write-through Redis) e tabela editável inline em `/hub/llm-custo`.

### 14.3 Fase 3 — Telemetria detalhada
- `observability_repository.py::get_metricas_por_provider` passou a trazer
  `tokens_entrada`/`tokens_saida`/`cache_hit_pct` por provider.
- `/hub/llm-custo`: colunas novas na tabela por provider + aviso explicando
  os dois caches (semântico, Redis, já mede `cache_hit_pct`; de prompt do
  provider, tipo o `cache_por_1m` do DeepSeek, **não implementado em
  nenhum adapter ainda**).

### 14.4 Fase 4 — migração de call sites (escopo revisado)
Dos "6 call sites restantes" do §13.6 item 3, só 5 são geração de texto de
verdade (`genai.Client(...).generate_content`) e migraram pra
`get_llm_provider()`: `beat_nightly_memory.py`, `calendar_llm_adapter.py`
(síncrono, usa `gerar_resposta_sincrono`), `graph_extractor_service.py`,
`query_transform.py`, `memory_summarizer.py` — cada um com uma `rota` nova
própria, visível em "custo por rota" no hub. O 6º,
`gemini_stt_provider.py`, é transcrição de áudio (Gemini multimodal) — não
se encaixa em `ILLMProvider` (DeepSeek/Groq não fazem STT), fica de fora
deliberadamente. RBAC-na-`main` e unificação de telemetria de voz ficaram
fora do escopo (dependem de decisão própria, não são só código).

### 14.5 Achado real #1 — `.env` nunca existia dentro do container
Ao testar o `POST /system/env` contra o Docker real, deu 500:
`PermissionError` tentando criar um tempfile em `/app`. Investigando:
`docker-compose.yml` só tinha `env_file: .env` (âncora `x-app-env`) — isso
injeta as variáveis como env vars do processo no boot, mas **não monta o
arquivo `.env` dentro do container**. `/app/.env` simplesmente não
existia. Fix: adicionado `- ./.env:/app/.env` nos volumes do serviço `api`
(`docker-compose.yml`), com container recriado (`docker compose up -d
api`, não só restart — mudança de volume exige recriar).

### 14.6 Achado real #2 — mismatch de UID host↔container
Com o `.env` montado, o erro mudou pra `PermissionError` de verdade: o
arquivo é do host (`khachy`, uid 1000), o processo dentro do container
roda como `oraculo` (uid 1001, `Dockerfile` linha 46-47) — sem grupo em
comum. `chgrp`/`chmod` exigem root, que eu não tenho no host. Usuário
rodou `sudo chgrp 1001 .env && chmod 664 .env` — meio caminho andado.

### 14.7 Achado real #3 — `dotenv.set_key` precisa de permissão no DIRETÓRIO, não no arquivo
Mesmo com o `.env` liberado pro grupo do container, o 500 continuou.
Causa: `python-dotenv`'s `set_key()` faz escrita atômica (cria um
tempfile no MESMO DIRETÓRIO do alvo, depois renomeia por cima) — ou seja,
precisa de permissão de escrita em `/app` (o diretório), não só no
`.env`. `/app` é `root:root` (nunca veio de um `COPY --chown`, só das
subpastas). Fix: troquei `set_key()` por uma escrita in-place
(`_gravar_env_inplace`, `admin_api.py`) que abre o MESMO arquivo com
`open(path, "w")` — só precisa de permissão no arquivo, não no diretório.

### 14.8 Achado real #4 — corrupção por falta de newline final
Primeiro teste da escrita in-place corrompeu o `.env`: a última linha do
arquivo (`GitHub_Api_Key=...`) não tinha `\n` no final, e meu código
apendava a chave nova (`DEEPSEEK_MODEL=...`) direto na lista de linhas sem
garantir separador — resultado: as duas chaves grudaram numa linha só
(`...oumDEEPSEEK_MODEL="deepseek-chat"`), inutilizando ambas. Corrigido o
`.env` manualmente e o código (`_gravar_env_inplace` agora garante `\n` no
final da última linha existente antes de apensar chaves novas).

### 14.9 Achado real #5 — minha própria edição resetou a permissão do §14.6
Ao corrigir a corrupção do §14.8 com a ferramenta de edição, o arquivo foi
reescrito por inteiro — o que troca o inode e reseta dono/grupo pro
padrão do processo que escreveu (`khachy:khachy` de novo, perdendo o
`chgrp 1001` do usuário). Pendência registrada: usuário precisa rodar
`sudo chgrp 1001 .env && chmod 664 .env` **de novo** antes do próximo
teste do `POST /system/env`. Lição pra próxima sessão: qualquer
reescrita-inteira do `.env` (editor externo, `git checkout`, etc.) derruba
essa permissão — só a escrita in-place do próprio endpoint (§14.7) não
mexe no dono/grupo, porque usa o mesmo inode.

### 14.10 `/hub/chat` estava desatualizado — reescrito pro pipeline real
Ao tentar validar a Fase 4 sem WhatsApp (usuário sem celular), tentei
indicar `/hub/chat` como forma de testar — usuário lembrou que esse
simulador está desatualizado. Investigando, confirmei: `chat_stream()`
(`hub.py`) reimplementava manualmente uma orquestração PRÓPRIA e ANTIGA
(`router.supervisor.rotear()` + `application.chain.planner.criar_plano()`
+ `application.runtime.dispatcher._despachar_workers()` + uma máquina de
estados HITL própria em Redis pra SIGAA/mídia), enquanto o WhatsApp real
(`process_message_task.py` linha 405) já usa incondicionalmente
`dispatcher_langgraph.processar()` (LangGraph, `AsyncRedisSaver`, resume
de `interrupt()` automático, delega SIGAA/comandos/GREETING pro
`dispatcher.py` original que já tem seu próprio HITL de SIGAA embutido).
Ou seja: dois pipelines paralelos, um deles morto/divergente.

Fix: reescrevi `chat_stream()` inteiro (~450 linhas → ~65) pra chamar
`dispatcher_langgraph.processar()` direto, igual o WhatsApp faz — mesma
`user_context`, mesma persistência de memória (`mem_svc.persistir_turno`),
mesmos eventos SSE que o frontend (`static/js/chat-debugger.js`, que é
agnóstico ao número/nome dos steps) já consumia. Testado ao vivo via curl
com cookie de admin: `"Oi"` → `GREETING` real; pergunta de calendário →
`CALENDARIO` real (RAG + LLM, ~25s); confirmado em `metricas_llm` que a
rota `query_transform` (Fase 4) foi exercitada de verdade pelo pipeline de
produção. Efeito colateral bom: funis de ticket/CRUD agora resumem entre
mensagens no simulador (checkpointer do LangGraph), sem nenhum código
extra.

### 14.11 Estado no fim da sessão
- Fases 1-4 implementadas, migration 008 aplicada no Postgres real
  (8 linhas seedadas, confirmado via `psql`), container `oraculo_api`
  recriado com o `.env` montado e rodando código novo, `pytest tests/unit`
  com as mesmas 14 falhas pré-existentes (nada novo quebrado — confirmado
  rodando os mesmos testes no `git stash` antes das mudanças).
- `/hub/chat` reescrito e validado contra o pipeline real.
- **Pendente pra abrir a próxima conversa**: usuário precisa rodar `sudo
  chgrp 1001 .env && chmod 664 .env` de novo (§14.9) antes de eu poder
  confirmar que `POST /api/admin/system/env` grava de ponta a ponta sem
  corromper o arquivo.

## 15. Sessão 2026-08-25 — Mega auditoria + organização/limpeza documental

### 15.1 O que motivou esta sessão

Auditoria completa do repositório em 2026-08-24 (relatório publicado como
artifact, não versionado no repo) identificou que `.claude.md` tinha crescido
para um diário de sessão de 46KB/80 linhas densas em vez de um arquivo de
regras — causa raiz: a própria regra 4 do arquivo ("se corrigir um padrão,
atualize `.claude.md` automaticamente") nunca tinha um mecanismo de resumo.
Nesta sessão, `.claude.md` foi reescrito para ficar enxuto (contexto +
arquitetura resumida + regras + comandos + convenções + restrições +
ponteiros pra documentação detalhada) e o conteúdo cronológico que estava
misturado nele foi consolidado aqui. A maior parte já estava coberta em
detalhe nas seções 7-13 acima (LangGraph, rest_lab/mcp_lab, multimodal,
multi-provider LLM) — o que segue abaixo é só o que era **exclusivo** de
`.claude.md` e não tinha sido registrado em nenhuma seção anterior.

### 15.2 Achados técnicos do SDK `mcp` (versão `mcp==2.0.0`, testado 2026-08-01)

A API pública do client HTTP diverge da documentação genérica do protocolo:
- É `mcp.client.streamable_http.streamable_http_client` (não
  `streamablehttp_client`, como a doc costuma grafar).
- O context manager devolve só `(read, write)` — 2 valores, não 3 como em
  versões anteriores do SDK.
- `CallToolResult` usa `is_error` (snake_case), não `isError`.

Se a versão pinada em `requirements.txt` for atualizada, testar esses três
pontos de novo antes de assumir que continuam iguais.

### 15.3 Gateway MCP da pipeworx.io — doc erra parâmetros, testado ao vivo (2026-08-02)

`gateway.pipeworx.io/<pack>/mcp` (StackExchange, `brave-search`, `github`)
expõe um catálogo grande de tools "de plataforma" junto com as do pack
anunciado — `list_tools()` retorna tudo junto. Achados por tentativa/erro
(o SDK `mcp==2.0.0` valida a resposta contra `output_schema` e estoura
`RuntimeError`/`ExceptionGroup` sem expor o payload cru — só dá pra ver via
`exception.__cause__.instance`):

- **Autenticação BYO key**: não é `?_apiKey=` na URL do gateway (como a doc
  diz) — é o argumento `_apiKey` (camelCase, maiúscula no K) dentro do
  próprio `call_tool(nome, args)`. `_apikey` (minúsculo) falha silenciosamente.
- `web_search`: parâmetro é `query` (doc dizia `q`). Resposta:
  `{"query","altered","total","returned","results":[{"title","url","description","age",...}]}`.
- `search_repos` (GitHub): parâmetro `query` bateu com a doc, mas a resposta
  não — chave raiz é `repos` (não `items`/`repositories`), campos `stars`/`url`
  (não `stargazers_count`/`html_url`).
- `get_user` (GitHub): resposta tem `url` (não `html_url`); `bio`/`email`/
  `twitter` podem vir `null`.
- `brave_image_search`/`brave_video_search` **não existem** no proxy da
  pipeworx (só `web_search`/`news_search`) — busca de imagem implementada
  como REST direto (`api.search.brave.com/res/v1/images/search`, header
  `X-Subscription-Token`, mesma `BRAVE_API_KEY`), fora do protocolo MCP.
  `results[0].properties.url` é a URL de verdade da imagem (usável direto em
  `enviar_midia_url`); `results[0].thumbnail.src` é só a miniatura.

### 15.4 Bug de regex de pluralização em português (2026-08-02)

Achado real, não é typo: `imagens?` em regex significa "imagen" + "s"
opcional (casa "imagen"/"imagens"), **nunca** "imagem" (com M) — português
pluraliza "imagem"→"imagens" trocando M por N antes do S (irregular),
diferente do inglês onde só se acrescenta "s". `brave imagem <termo>`
(singular, a forma que qualquer usuário real digita) sempre caía no
fallback de "não reconheci". Fix: `(?:imagem|imagens)` explícito em vez de
`imagens?`. **Lição para qualquer regex nova em português**: nunca assumir
que `palavra + "s?"` cobre o plural — conferir pluralização irregular
(`-agem`→`-agens`, `-ão`→`-ões`/`-ães`, etc.) antes de usar o atalho `?`.

### 15.5 Instalar pacote Python num container já rodando, sem rebuild (achado operacional)

O container tem dois Pythons — um "de sistema" (`/usr/local`, com `pip`
normal) e o venv da app (`/opt/venv`, sem `pip` embutido, sem permissão de
escrita pro usuário não-root). `docker compose exec worker pip install X`
instala no Python errado (só falha depois, quando o import quebra em
produção). Forma que funciona: `docker compose exec -u root worker pip
--python /opt/venv/bin/python3 install X`, depois `docker compose restart
worker`. Atalho de teste rápido — não substitui rebuild de imagem.

### 15.6 Reorganização documental executada nesta sessão

Ver relatório completo entregue ao usuário ao final desta sessão para a
lista línea-a-linha do que foi movido/removido/criado. Resumo:
- Criada `docs/` (`architecture/`, `business/`, `decisions/`, `historico/`,
  `assets/`); 6 documentos `.md` da raiz + 6 artefatos binários movidos para
  lá com `git mv` (histórico preservado).
- `docs/architecture/arquitetura_oraculo.md` corrigido (tabela de filas
  Celery e de model-routing, ambas desatualizadas — ver §9.8/§13 acima) e
  promovido a fonte oficial de arquitetura técnica.
- `.claude.md` reescrito enxuto; este §15 é o destino do conteúdo
  cronológico que estava misturado nele.
- 16 arquivos confirmadamente mortos removidos (8 `.bak`, mais módulos com
  zero importadores reais verificados por grep + suíte de testes antes/depois
  — 258 passed/13 falhas pré-existentes, inalterado). Lista completa no
  relatório final.
- `test_timeout.py` (raiz) removido — importava
  `src.application.routing.semantic_router`, módulo que não existe mais
  (só sobrou `command_builder.py` em `application/routing/`); órfão
  confirmado, zero referências no repo.
- **Não tocado, decisão explícita**: `src/services/` (migração incompleta
  services/→capabilities/, já sinalizada como fora de escopo em
  `docs/historico/PLANO_REFATORACAO_SUPERVISOR.md` §0.1b), os 4 `Protocol`
  de `domain/ports/` sem implementador, `run_eval_docker.py` duplicado
  (raiz vs. `src/`), e `tests/test_wiki_scraper.py` (órfão, importa
  `src.domain.tools.tool_wiki_ctic`, que não existe mais) — todos exigem
  decisão de produto antes de qualquer remoção, não são "óbvios" o
  suficiente pra essa sessão de organização decidir sozinha.

## 16. Sessão 2026-08-25 (continuação) — Execução do plano de integração LangGraph/REST/MCP, Fases 0-6

Sessão seguinte à mega auditoria (§15): o usuário aprovou as 7 decisões do
plano de integração (merge da branch inteira atrás de flags; LangGraph
assume 100% da produção; RAG/síntese do LangGraph via Celery; REST/MCP com
camada de Application; checkpointer em DB Redis dedicada; testes HITL
corrigidos antes; Dockerfile com `COPY rest_lab/`/`mcp_lab/`) e pediu
execução completa até a Fase 10, com check-in obrigatório só antes da Fase
2d (maior risco técnico) e em falha de checkpoint. Branch de integração:
`integration/langgraph-rest-mcp` (a partir de `research/rest-mcp-estudos`),
worktree própria em `/mnt/storage/projects/Oraculo-integration`.

### 16.1 Fase 0-1 — baseline e flags

Reorganização documental pendente da sessão anterior (§15) commitada
separada do trabalho de integração. 4 feature flags novas em
`settings.py`, todas desligadas por padrão: `FEATURE_LANGGRAPH_CELERY_DISPATCH`,
`FEATURE_LANGGRAPH_NATIVE_ROUTES`, `FEATURE_REST_PRODUCT`,
`FEATURE_MCP_PRODUCT`.

### 16.2 Fase 2a — isolamento de Postgres nos 10 testes HITL + 2 bugs reais achados

`rbac.py::checar_permissao_chamado()` chama `buscar_pessoa_por_telefone()`,
que abre conexão real ao Postgres — sem banco no teste, os 10 cenários
HITL (`test_langgraph_crud_hitl.py`/`test_langgraph_ticket_hitl.py`)
falhavam desde sempre com erro de conexão, nunca chegando a exercitar
lógica real. Fixture mockando o lookup + `DEV_TEST_SKIP_REGISTRATION`
resolveu o isolamento — e revelou 2 bugs nunca antes exercitados:
1. Mock de `responder_rag_direto()` desatualizado num teste (ganhou
   `rota=`/`session_id=` na Fase 3.5, commit `0a6e7e9`, mas o teste nunca
   tinha rodado até esse ponto).
2. `ticket_confirm()`/`crud_confirm()`: "cancelar" na pergunta de
   confirmação caía no comando global de saída (`_eh_saida`, mais
   genérico) em vez do "não" específico que `validar_confirmacao`/
   `_RE_NEGA` já reconhecia pra essa pergunta — resultado era o texto
   errado ("🚪 Você saiu...") em vez de "❌ Ticket cancelado."/"❌
   Atualização cancelada.". Corrigido invertendo a ordem de checagem nos
   dois nodes de confirmação (só neles — os nodes de pergunta continuam
   checando saída primeiro).

### 16.3 Fase 2b — RAG/síntese via Celery (Decisão 02)

`responder_rag_direto()` ganha `_responder_rag_via_celery()` — mesmo
chord `rag_search`→`synthesis` que o Planner legado já usa, mas aguardado
(`asyncio.to_thread(async_result.get, timeout=...)`) em vez de
fire-and-forget, porque o node do grafo precisa do texto de volta pra
continuar. Atrás de `FEATURE_LANGGRAPH_CELERY_DISPATCH`. **Só testado com
mocks** — o teste de carga real contra workers `rag_search`/`synthesis`
vivos que a Decisão 02 pede não foi feito (sem Docker/Redis/Celery no
ambiente onde isso foi implementado); fica pendente pro usuário rodar num
ambiente real antes de considerar a Decisão 02 fechada de verdade.

### 16.4 Fase 2c — checkpointer isolado (Decisão 04)

`AsyncRedisSaver` passa a usar `REDIS_URL` com DB `/3` em vez de `/0`
(mesmo padrão de derivação que `celery_app.py` já usa pro broker `/1` e
result backend `/2`).

### 16.5 Fase 2d — nodes nativos + achado de segurança não previsto no plano

Antes de portar as rotas, achado real ao investigar: `dispatcher_langgraph.py`
nunca rodava `InputGuardrail` (prompt injection/rate limit) nem
`handle_hitl_continuation` (HITL legado do SIGAA, `hitl:session:*`) —
dependia 100% de delegar pro `dispatcher.py` original, que roda os dois no
topo. Isso já deixava GERAL/CALENDARIO/EDITAL/CONTATOS/WIKI/TICKET_ABERTURA/
CRUD (nunca delegadas) sem guardrail nenhum, hoje, antes de qualquer coisa
desta sessão — e migrar SIGAA sem corrigir isso tornaria o gap permanente
(Decisão 01: `dispatcher.py` vira só debug/eval) e quebraria o login do
SIGAA de verdade (CPF/senha digitados no meio do funil sendo
reclassificados como pergunta RAG). Corrigido como pré-requisito: os dois
checks agora rodam direto em `dispatcher_langgraph.py::processar()`.

Só depois disso, as 4 rotas (CHECK_STATUS/GREETING/MEDIA_DOWNLOAD/SIGAA)
viraram nodes nativos do grafo, atrás de `FEATURE_LANGGRAPH_NATIVE_ROUTES`.
SIGAA reaproveita `start_or_continue_sigaa()` (zero duplicação); os outros
3 reimplementam a lógica do fast-path equivalente em `dispatcher.py`
(aceito por ora — `dispatcher.py` vira debug/eval-only ao fim da Decisão
01).

Fase 2 fechada com cobertura de teste pro RBAC (bloqueio nomeado no ADR
0001, zero testes existiam antes: `domain/permissions.py`,
`agents/tickets/rbac.py`) e TD-013 registrado (Gatekeeper reescreve toda
decisão `IGNORE` pra `LLM` incondicionalmente — pré-existente em `main`,
não corrigido, fora do escopo das 7 decisões).

### 16.6 Fases 3-4 — REST/MCP ganham camada de Application (Decisão 03)

`RestLabUseCase`/`McpLabUseCase` novos em `src/application/use_cases/` —
`rest_lab/tools.py`/`mcp_lab/tools.py` viram facades finos, `router.py`/
`run_test.py` de nenhum dos dois mudou uma linha. `mcp_lab/tools.py::buscar_imagem()`
parou de instanciar `EvolutionAdapter` direto (único ponto de todo
`rest_lab`/`mcp_lab` que tocava infraestrutura de produção sem camada
intermediária) — passa pela nova capability
`evolution_tool.py::enviar_midia_por_url()`. ADRs 0005/0006. Nenhum dos
dois labs muda de propósito — continuam laboratórios de estudo.

### 16.7 Fase 6 — Dockerfile (Decisão 06)

`COPY rest_lab/`/`COPY mcp_lab/` adicionados — antes só chegavam ao
container via bind-mount do compose. Validado com `docker build` real
pelo usuário após o fechamento desta sessão (2026-08-25): build completo
sem erro, `docker run --rm oraculo-test python -c "import rest_lab,
mcp_lab"` devolveu `ok`. Decisão 06 fechada de verdade.

### 16.8 CI real via PR #1 — achado que o sandbox local não pegava

Nenhum commit tinha sido enviado ao remoto; branch empurrada e PR #1
aberto (sem merge) só pra disparar o workflow real (Redis+Postgres de
verdade, diferente deste sandbox). 1ª rodada: `test_dispatcher_nao_vaza_estado_entre_tickets_na_mesma_sessao`
falhou — com Redis real, o rate limit de verdade do `InputGuardrail`
(recém-adicionado na Fase 2d) barrava a 9ª mensagem de uma sequência de
~12 chamadas ao `dlg.processar()` na mesma sessão sem pausa (o teste
simula 2 tickets seguidos rápido). Localmente o rate limit sempre
degradava silencioso (sem Redis), então nunca apareceu. Não é bug de
produto — o mesmo rate limit já existe em `dispatcher.py::processar()`
pra ticket/CRUD desde sempre; só restaura paridade. Corrigido
neutralizando o sub-check de rate limit nos testes que chamam
`dlg.processar()` diretamente. 2ª rodada: verde, exceto o único teste
pré-existente já classificado UNRELATED na auditoria original
(`test_cognitive_os_sigaa_route_requires_auth_flow`, decisão explícita do
usuário de não mexer — fora do escopo das 7 decisões aprovadas).

### 16.9 Pendências explícitas que dependem de infra real (não deste sandbox)

1. ~~Teste de carga do despacho Celery de RAG/síntese~~ — **fechado
   2026-08-25**, ver §16.10.
2. ~~`docker build` real validando o `COPY rest_lab/`/`mcp_lab/`~~ — **fechado
   2026-08-25**, ver §16.7.
3. Teste manual via WhatsApp real das rotas nativas do LangGraph antes de
   ligar `FEATURE_LANGGRAPH_NATIVE_ROUTES`/`FEATURE_LANGGRAPH_CELERY_DISPATCH`
   em produção (Fase 2d/2b).
4. `tests/integration/`/`tests/e2e/` não rodados (precisam de uvicorn/LLM
   real) — 4 arquivos em `tests/e2e/` descobertos órfãos nesta sessão
   (import quebrado, pré-existente, ver TD-014), não relacionados a esta
   integração.

### 16.10 Teste de carga do despacho Celery (Decisão 02) — fechado 2026-08-25

Usuário rodou o stack real (`docker compose --profile core --profile app up -d
redis worker_rag worker_synthesis`, mais `api`) com
`FEATURE_LANGGRAPH_CELERY_DISPATCH=true` e um script ad-hoc
(`responder_rag_direto()` chamado 10x em paralelo, sessões diferentes).
10/10 concluíram sem timeout/exceção, latência 4.9-18.6s (dentro do
orçamento `RAG_SEARCH_TIMEOUT_S+SYNTHESIS_TIMEOUT_S=22s`). Decisão 02
fechada de verdade.

No meio do caminho, achado real e **não relacionado a esta integração**:
`worker_rag`/`worker_synthesis` (e por extensão qualquer worker Celery)
crashavam no boot com `PermissionError: [Errno 13] Permission denied:
'/tmp/sigaa_downloads'`. Causa: `celery_app.py::include=[...]` importa
TODOS os módulos de worker no boot de qualquer processo, independente de
`--queues` — então `worker_rag` importa `worker_sigaa.py` →
`agents/sigaa/service.py` → `capabilities/sigaa/browser.py`, que cria
`DOWNLOAD_DIR` (`/tmp/sigaa_downloads`) **como efeito colateral do
import**, não sob demanda. Como `/tmp` do container é bind-mount de
`dados/tmp` no host, um `dados/tmp` sem permissão de escrita pro usuário
não-root do container (`oraculo`, uid 1001) derruba QUALQUER worker no
boot, mesmo um que nunca usa SIGAA. Corrigido no ambiente do usuário
(`chown`/`chmod` em `dados/tmp`), não no código — é fragilidade real
(import com side effect + registro de tasks compartilhado entre todos os
workers), mas fora do escopo das Decisões 00-06. Candidato a TD futuro se
o usuário confirmar que quer registrar.
