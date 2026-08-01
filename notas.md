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
