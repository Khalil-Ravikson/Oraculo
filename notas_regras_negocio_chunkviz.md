# Notas — Regras de Negócio do ChunkViz (scraping Wiki CTIC)

> Rascunho de discussão. Nada aqui foi implementado — é só registro do assunto
> para retomarmos depois. Sem decisão de arquitetura ainda.

## Contexto

Bug corrigido em 2026-07-16: `POST /chunkviz/extract-url`
([src/api/routers/web/hub.py](src/api/routers/web/hub.py)) quebrava com
"Formato '' não suportado" ao raspar a wiki do CTIC. Causa: o endpoint chamava
`save_temp_file(file_id, ...)` passando o hash `file_id` (sem extensão) como
nome de arquivo — `save_temp_file` deriva a extensão do nome e rejeita `""`.
Também sobrescreveria o conteúdo raspado com o `repr()` do dict de metadados.
Corrigido para gravar o JSON de metadados diretamente, sem reusar
`save_temp_file` para esse fim.

## Pontos em aberto para discussão futura

1. **Duplicação de scrapers da wiki CTIC** — existem hoje três
   implementações distintas:
   - `src/infrastructure/scraping/implementations/uema_wiki_scraper.py` (`UEMAWikiScraper`)
   - `src/infrastructure/scraping/implementations/generic_scraper.py` (outra classe `UEMAWikiScraper`, duplicada)
   - `tests/test_wiki_scraper.py` referencia `src/domain/tools/tool_wiki_ctic.py`, que **não existe mais** no repo (provável resíduo de refatoração)
   Decidir: qual fica, quem consome cada uma, e o que fazer com o teste órfão.

2. **`/chunkviz/extract-url` sempre usa `GenericHTTPScraper`**, mesmo para
   URLs `ctic.uema.br`/`wiki.uema.br` — nunca aciona o `UEMAWikiScraper`
   especializado (que preserva hierarquia de headers/Markdown). Definir se
   o ChunkViz deve rotear por domínio para o scraper especializado.

3. **Regra de negócio de ingestão via ChunkViz** — ainda não decidido:
   quais domínios podem ser raspados via essa tela, se precisa de
   allowlist, e como o `doc_type`/taxonomia (`eixo`, `setor`, `ano`) deve
   ser aplicado a conteúdo de wiki.

## Próximos passos

- Conversar sobre os pontos acima antes de qualquer mudança de aplicação.
- Nenhuma nova app/serviço deve ser adicionada ao projeto até essa decisão.

---

## Notas — Escopo dos Agentes e Conversação Usuário-Agente (2026-07-16)

> ⚠️ Objeção/aviso: esta é uma decisão de escopo **provisória**, tomada pra ter
> algo funcionando de forma simples e testável agora. Pode (e provavelmente
> vai) mudar quando o projeto amadurecer — não tratar como arquitetura final.

### Decisão de escopo: só 3 agentes "ativos" por enquanto

1. **RAG** (`agents/academic_knowledge/`) — mantido como está, é o mais maduro.
2. **Cadastro** (`agents/conversation/registration.py` — `RegistrationFunnel`)
   — já não é uma "tool" de LLM (é máquina de estado fixa). HITL/interrupção
   ainda não implementada (ver discussão de Opções A/B/C mais abaixo/acima
   no histórico da conversa — recomendação foi B-lite: só reaproveitar o
   regex puro do Supervisor pra saudação, sem chamar worker pesado no meio
   do cadastro). **Ainda não implementado**, só decidido o desenho.
3. **CR / Dados Institucionais** (`agents/sigaa/service.py::consultar_indice`)
   — já existe e funciona: abre o SIGAA autenticado do aluno e lê o bloco
   único da página que traz Matrícula, Curso, Nível, Status, E-mail, Entrada
   **e** CR/IRA/Integralização. `"cr"/"ira"/"rendimento"/"coeficiente"` já
   mapeiam pro worker `sigaa_indice` (`sigaa_use_cases.py`).

**Pausados/fora do escopo por ora:** os outros 7 sub-fluxos do SIGAA
(biblioteca, notas, histórico, estrutura, turmas, extensão, processos
seletivos) e o agente `tickets` original (GLPI fake + envio de e-mail sem
credencial configurada — já estava dormente em produção antes desta mudança,
o próprio código admitia isso).

### O que foi implementado nesta rodada

- **`is_agent_enabled()` conectado de verdade.** Antes, o painel `/hub/agents`
  só gravava uma flag no Redis (`admin:agent:{nome}:enabled`) que nada no
  caminho de produção verificava — liga/desliga era cosmético. Agora:
  - `runtime/dispatcher.py::processar()` tem um gate central
    (`_ROTA_PARA_AGENTE`) que verifica `is_agent_enabled` antes de despachar
    RAG (`academic_knowledge`), SIGAA (`sigaa`) ou CRUD (`tickets`).
  - `application/tasks/process_message_task.py::_handle_message` verifica
    `is_agent_enabled(r, "conversation")` antes de entrar no funil de cadastro.
  - GREETING e MEDIA_DOWNLOAD continuam sempre ligados (são fast-paths
    utilitários, não "agentes" no sentido de negócio).
- **Agente `tickets` repropósito para algo simples e real:** em vez do GLPI
  fake / envio de e-mail sem credencial, agora expõe só
  `!atualizaremail seu@email.com` — atualiza o e-mail do próprio cadastro
  (escopado pelo telefone de quem envia, não por matrícula arbitrária, pra
  não abrir brecha de um usuário alterar o e-mail de outra matrícula).
  Arquivo novo: `application/commands/cmd_atualizar_email.py`.
- **Menu "Ferramentas do usuário"** adicionado à saudação (GREETING, tanto
  no fast-path de `dispatcher.py` quanto em `worker_greeting.py`): explica
  que existem `!ytb` (baixar vídeo do YouTube) e `!sticker` (criar
  figurinha) — comandos ainda stub/placeholder, só para demonstração.

### Ainda pendente (não implementado nesta rodada)

- HITL/interrupção real no funil de cadastro (Opção B-lite discutida).
- Restringir o SIGAA para responder só a CR/Dados Institucionais e ignorar
  os outros 7 sub-fluxos (hoje eles continuam tecnicamente alcançáveis se o
  usuário usar a palavra-chave certa — só o agente inteiro "sigaa" pode ser
  desligado via `is_agent_enabled`, não sub-fluxo a sub-fluxo).
- Decidir se `!ytb`/`!sticker` continuam como stub para sempre ou se um dia
  ligam nos workers reais (`worker_ytb_download`/`worker_insta_download`,
  que já funcionam via link colado em texto livre, caminho independente).
