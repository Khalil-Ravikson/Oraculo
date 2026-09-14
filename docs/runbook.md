# Runbook — Oráculo

> Escrito em 2026-09-11. **Todo sintoma listado aqui aconteceu de verdade**,
> durante a rodada de recuperação do v1 — nenhum é hipotético. O comando de
> diagnóstico ao lado de cada um é o que foi efetivamente usado para
> identificá-lo.
>
> Organizado por sintoma, não por componente: você chega aqui com um
> problema, não com um índice.
>
> Estado do sistema: [`ESTADO_ATUAL.md`](ESTADO_ATUAL.md).
> Arquitetura: [`architecture/arquitetura_oraculo.md`](architecture/arquitetura_oraculo.md).

---

## 0. Os três comandos que você sempre precisa

```bash
docker compose ps                      # quem está de pé (e saudável)
docker compose logs -f worker          # onde as mensagens são processadas
docker compose logs -f api             # onde o painel e o webhook vivem
```

O `docker compose up -d` **não sobe nada sem `COMPOSE_PROFILES` no `.env`**
(`core,monitoring,app,gateway`). Se o comando "funcionou" e nada subiu, é
isso.

---

## 1. O bot parou de responder

Antes de investigar, responda nesta ordem — cada passo elimina uma causa
inteira.

### 1.1 Os containers estão de pé?

```bash
docker compose ps --format "{{.Service}} {{.Status}}"
```

**Já aconteceu:** uma limpeza de Docker (`docker system prune` e parentes)
parou o Redis e o exportador. Eles saem com `Exited (143)` — SIGTERM, saída
limpa, **sem perda de dado** (o Redis grava antes de sair). O sintoma
indireto é `Name or service not known` nos logs do worker: o container não
existe, então o nome não resolve.

```bash
docker compose up -d                   # sobe tudo o que caiu
```

### 1.2 O Redis está recusando escrita?

```bash
docker compose exec -T redis redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human"
```

**Já aconteceu, e foi o pior caso da rodada.** Com a memória no teto, o
Redis recusa **toda** escrita:

```
OutOfMemoryError: command not allowed when used memory > 'maxmemory'
```

O efeito vai muito além do que estava escrevendo: sem escrita não há posição
de menu, não há cache, não há checkpoint de conversa. **O bot para.**

A causa raiz (embedding gravado como texto) foi corrigida — ver
[`architecture/arquitetura_oraculo.md` §8.1](architecture/arquitetura_oraculo.md).
Se acontecer de novo, o alívio imediato é liberar espaço:

```bash
# Quanto cada coisa ocupa
docker compose exec -T redis redis-cli --scan --pattern "rag:chunk:*" | wc -l

# Descartar os trechos de uma fonte específica (a wiki, por exemplo)
docker compose exec -T redis sh -c \
  'redis-cli --scan --pattern "rag:chunk:https://ctic.uema.br*" | while read k; do redis-cli DEL "$k" >/dev/null; done'
```

Isso é seguro no sentido que importa: o conteúdo está na wiki e pode ser
ingerido de novo. O que **não** se pode apagar sem pensar é
`checkpoint:*` (conversas em andamento) e `handoff:session:*`.

> **Não troque a política para `allkeys-lru`.** A `volatile-lru` atual só
> evicta chaves com prazo de validade, o que protege os trechos do RAG e os
> checkpoints. Foi ela que transformou o estouro num erro explícito em vez de
> corromper a busca em silêncio.

### 1.3 A sessão está em atendimento humano?

Uma conversa encaminhada a um atendente fica **muda por 24 horas**. Isso é
esperado, e é fácil confundir com "o bot quebrou".

Abra **Atendimento humano** (`/hub/handoffs`) no painel: ele lista as
conversas pausadas e devolve qualquer uma ao bot com um botão. Pelo WhatsApp,
o equivalente é `$voltar <jid>` (comando de admin).

**Já aconteceu:** quem testava pelo simulador de chat do painel ficava preso,
porque o `$voltar` não alcança o simulador. A página existe por causa disso.

### 1.4 A pessoa bateu no limite de mensagens?

```bash
docker compose logs --tail=100 api | grep "GUARDRAIL RATE"
```

O limite vale **só para pergunta** — navegar o menu não conta. Ajuste em
`/hub/config` (`RATE_LIMIT_MSGS`, `RATE_LIMIT_WINDOW_S`), vale na hora. Para
destravar alguém específico:

```bash
docker compose exec -T redis redis-cli DEL "rl:msg:<session_id>"
```

### 1.5 O disjuntor do provedor abriu?

Cinco falhas do provedor na janela abrem o circuito, e o bot passa a recusar
a rota. **Ele não troca de provedor sozinho** — a decisão é sua.

Feche em `/hub/llm-custo`, no botão de reset do provedor.

---

## 2. O bot responde, mas diz que não encontrou nada

O menu funciona e a pergunta chega ao RAG, mas a resposta é sempre "não
encontrei". É problema de **conteúdo**, não de código.

```bash
# O que o assistente conhece, por assunto
curl -s -b cookie.txt http://localhost:9001/hub/wiki/indice
```

Ou pela página **Wiki da CTIC** no painel, que mostra a mesma coisa.

Duas causas, nesta ordem de probabilidade:

**O conteúdo entrou com o assunto errado.** As buscas do menu procuram em
`wiki_ctic` e `contatos`. Documento ingerido como `geral` fica **invisível**
para o menu. Ao ingerir por `/hub/chunkviz`, escolha o campo **Assunto**.

**O índice perdeu o conteúdo.** Já aconteceu depois de um restart do Redis: as
chaves continuavam no banco e o índice mostrava zero.

```bash
# Documentos no índice x chaves no banco — se divergirem muito, é isso
docker compose exec -T redis redis-cli FT.INFO idx:rag:chunks | grep -A1 num_docs
docker compose exec -T redis redis-cli --scan --pattern "rag:chunk:*" | wc -l
```

A saída é reingerir. Não existe comando de "reindexar o que já está lá".

---

## 3. Mudei o código e nada mudou

**Editar um `.py` com o container rodando não recarrega.** O volume atualiza
o arquivo, mas o processo Celery mantém os módulos antigos em memória.

```bash
docker compose restart worker          # fila default/admin — roteamento, menu
docker compose restart worker_rag      # fila rag_search
docker compose restart worker_synthesis
docker compose restart worker_media
docker compose restart api             # painel, webhook, endpoints
```

Reiniciar o worker errado **não dá erro nenhum** — só continua rodando código
velho, em silêncio. Na dúvida, reinicie `worker` e `api`.

Mudou `docker-compose.yml` (variável de ambiente, limite de memória)? Aí
`restart` não basta, o container precisa ser recriado:

```bash
docker compose up -d <serviço>
```

---

## 4. As migrations não aplicam

```bash
docker compose run --rm migration
docker compose exec -T postgres psql -U user -d oraculo -c "SELECT version_num FROM alembic_version;"
```

Deve chegar em **027**.

**Já aconteceu:** um id de revisão com 33 caracteres estourou a coluna
`alembic_version.version_num`, que é `varchar(32)`:

```
StringDataRightTruncationError: value too long for type character varying(32)
```

A transação inteira reverte, e **nenhuma migration a partir daquela é
aplicada, em ambiente nenhum**. Se acontecer: encurte o id da revisão (e o
`down_revision` que a referencia). Alargar a coluna consertaria só a máquina
onde você alargou.

---

## 5. As métricas não aparecem no Prometheus

```bash
curl -s "http://localhost:9090/api/v1/targets?state=active" \
  | python -c "import sys,json; [print(t['labels']['job'], t['labels']['instance'], t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]"
```

Devem aparecer **7 alvos, todos `up`**: a API, os quatro workers, o
exportador do Redis e o próprio Prometheus.

Dois detalhes que custaram tempo e não são óbvios:

* O diretório de métricas dos workers precisa ficar em **`/dev/shm`**, não em
  `/tmp`. O `/tmp` do worker é um bind mount 9p para o `C:\` do Windows, e o
  `mmap` compartilhado que o `prometheus_client` usa **não funciona** nesse
  sistema de arquivos — os arquivos nascem com o tamanho certo e ficam com
  zero entradas, sem erro nenhum.
* O diretório precisa existir **antes** do primeiro import que declara
  métrica. É criado no topo do `celery_app.py` por isso.

Métrica só aparece depois de ser incrementada pelo menos uma vez. Worker
recém-subido que ainda não processou nada serve um `/metrics` vazio — isso é
normal, não é falha.

---

## 6. Como reverter o que esta rodada mudou

Cada mudança grande tem um interruptor. Nenhuma exige deploy para desligar.

| Mudança | Como desligar | Efeito |
|---|---|---|
| Bot de menu | `FEATURE_MENU_BOT=false` | Volta o Supervisor a classificar por LLM. Existe como rollback, não como configuração — se a validação passar, o caminho `false` sai. |
| Rotas restritas ao v1 | `ROTAS_ATIVAS=` (vazio) | Todas as rotas voltam a ser alcançáveis. |
| Reescrita de query por LLM | `FEATURE_QUERY_TRANSFORM_LLM=true` | Religa a 2ª chamada de LLM por resposta. |
| Fan-out Celery do RAG | `FEATURE_LANGGRAPH_CELERY_DISPATCH=true` | Busca e síntese voltam para as filas dedicadas. |
| Menu editado pelo painel | Botão "Restaurar o texto original" em `/hub/menu` | Volta ao menu embutido no código, mantendo o histórico. |
| Topologia do grafo | Aba "Grafo de produção" → Histórico → Reverter | Restaura uma versão anterior como versão nova. |

Reverter **código** é `git revert` do commit; a branch é
`hub/redesign-htmx-infra`. O que **não** volta sozinho é o formato de
armazenamento dos trechos: voltar ao commit anterior exige recriar o índice e
reingerir, porque trechos em HASH e em JSON não convivem.

---

## 7. Backup antes de mexer no índice

Faça **antes** de qualquer `FT.DROPINDEX`, reingestão ou troca de modelo de
embedding.

```bash
# Postgres — dump completo
docker compose exec -T postgres pg_dump -U user oraculo > backup_oraculo_$(date +%Y%m%d).sql

# Redis — força a gravação em disco e copia o arquivo
docker compose exec -T redis redis-cli BGSAVE
docker compose cp redis:/data/dump.rdb ./backup_redis_$(date +%Y%m%d).rdb
```

O `BGSAVE` é assíncrono: confirme que terminou antes de copiar.

```bash
docker compose exec -T redis redis-cli INFO persistence | grep rdb_bgsave_in_progress
```

---

## 8. Quem avisar

* **O bot parou** e não é nenhum dos casos da seção 1 → responsável técnico do
  projeto.
* **Alerta de disjuntor aberto** chega por WhatsApp em `SUPPORT_GROUP_JID`. Se
  essa variável estiver vazia, cai no primeiro número de `ADMIN_NUMBERS`. Se
  nenhum dos dois existir, **o alerta não vai a lugar nenhum** — conferir isso
  é item do checklist de produção.
* **Conteúdo errado ou desatualizado** na resposta → é conteúdo de wiki, não
  é bug: corrige-se editando a wiki e reingerindo.
