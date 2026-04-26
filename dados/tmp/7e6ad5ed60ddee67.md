# Manual de Uso — Bot UEMA e Referência de Comandos de Sistema

**Tipo:** Manual Operacional + Referência Técnica  
**Versão:** 1.0  
**Audiência:** Administradores UEMA e Utilizadores Avançados  
**Data:** Março de 2026

---

## Parte A — Como Usar o Assistente Virtual UEMA

### A.1 Acesso e Primeiros Passos

O Assistente Virtual da UEMA está disponível **exclusivamente via WhatsApp** no número institucional. Para iniciar:

1. Salvar o contacto do bot na agenda do telemóvel
2. Enviar qualquer mensagem de texto
3. O bot responde automaticamente dentro de **3 a 10 segundos**

**Importante:** O bot funciona **apenas em conversas privadas** (não em grupos). Mensagens de áudio, vídeo e stickers sem legenda de texto são ignoradas.

### A.2 Tipos de Perguntas Suportadas

| Categoria | Exemplos de Perguntas |
|---|---|
| 📅 Calendário | "Quando começa o semestre 2026.1?", "Data da matrícula de veteranos" |
| 📋 Edital PAES | "Quantas vagas tem Engenharia Civil?", "Como funciona a cota BR-PPI?" |
| 📞 Contatos | "Email da secretaria de Direito", "Telefone da PROG" |
| 💻 TI e Sistemas | "Como acesso o SIGAA?", "Esqueci a senha do e-mail institucional" |
| 🎫 Suporte | "Computador do laboratório sem internet" (abre chamado no GLPI) |

### A.3 Limitações do Bot

O assistente **NÃO** consegue:
- Fazer matrícula ou cancelamento de disciplinas
- Acessar notas ou histórico académico individual
- Enviar documentos ou certificados
- Responder sobre assuntos não relacionados à UEMA

Para essas situações, procure directamente o setor responsável ou acesse o SIGAA em `sigaa.uema.br`.

### A.4 Rate Limits por Perfil

| Perfil | Mensagens por Minuto | Mensagens por Hora |
|---|---|---|
| Visitante (GUEST) | 10 | 50 |
| Aluno (STUDENT) | 30 | 200 |
| Administrador (ADMIN) | Sem limite | Sem limite |

Se receberes a mensagem *"Muitas mensagens seguidas!"*, aguarda 60 segundos antes de continuar.

---

## Parte B — Comandos Avançados para Administradores

### B.1 Comandos Disponíveis

Administradores podem enviar comandos especiais precedidos de `!` ou `/`:

| Comando | Sintaxe | Descrição | Resposta |
|---|---|---|---|
| Status | `!status` | Exibe estado do Redis, AgentCore e Cache | Imediata |
| Tools | `!tools` | Lista tools registadas no SemanticRouter | Imediata |
| Cache | `!limpar_cache` | Invalida todo o Semantic Cache | Background (~5s) |
| Ingestão (ficheiro) | `!ingerir nome.pdf` | Ingere ficheiro existente em /dados/ | Background (~2min) |
| Ingestão (anexo) | Anexar PDF + legenda `!ingerir` | Faz download e ingere | Background (~2min) |
| Fatos de aluno | `!fatos 5598999999` | Lista fatos long-term de um número | Imediata |
| Exportar RAGAS | `!ragas` | Exporta logs de produção como dataset | Background |
| Reload | `!reload` | Reinicia AgentCore e re-registra tools | Background (~10s) |

### B.2 Fluxo de Ingestão via WhatsApp

```
1. Admin envia PDF/CSV/DOCX com legenda "!ingerir"
2. Bot confirma: "📥 Ficheiro recebido! A ingerir em background..."
3. Celery worker baixa o ficheiro via Evolution API
4. Detecta extensão pelo mimetype
5. Salva em /dados/uploads/
6. Adiciona ao DOCUMENT_CONFIG dinamicamente
7. Executa chunking + embedding (BAAI/bge-m3, CPU)
8. Salva chunks no Redis Stack
9. Bot confirma: "✅ Documento ingerido! Chunks: 47"
```

**Tempo estimado de ingestão por formato:**

| Formato | Tamanho | Tempo Estimado |
|---|---|---|
| CSV (vagas) | < 50 linhas | ~15 segundos |
| PDF (texto) | ~10 páginas | ~45 segundos (PyMuPDF) |
| PDF (tabelas) | ~10 páginas | ~3 minutos (LlamaParse) |
| DOCX (manual) | ~20 páginas | ~30 segundos |

---

## Parte C — Referência de Comandos de Sistema Linux

*Esta secção documenta comandos úteis para manutenção do servidor onde o bot está hospedado. Incluída aqui propositadamente para testar se o LLM consegue distinguir contextos diferentes dentro do mesmo documento.*

### C.1 Gestão do Docker Compose

| Comando | Descrição |
|---|---|
| `docker-compose up -d` | Inicia todos os serviços em background |
| `docker-compose down` | Para todos os serviços |
| `docker-compose logs -f bot` | Acompanha logs do bot em tempo real |
| `docker-compose logs -f worker` | Acompanha logs do Celery worker |
| `docker-compose restart bot` | Reinicia apenas o bot |
| `docker-compose ps` | Lista estado de todos os serviços |

### C.2 Gestão do Redis

| Comando | Descrição |
|---|---|
| `redis-cli ping` | Verifica se Redis responde |
| `redis-cli FLUSHDB` | **APAGA TODOS OS DADOS** do DB actual |
| `redis-cli DBSIZE` | Número de chaves no DB actual |
| `redis-cli keys "rag:chunk:*"` | Lista chaves de chunks RAG |
| `redis-cli keys "cache:*"` | Lista entradas do Semantic Cache |
| `redis-cli TTL "rag:chunk:abc123"` | Tempo restante de um chunk |

**⚠️ ATENÇÃO:** `FLUSHDB` apaga todos os dados incluindo chunks ingeridos. O bot entra em "aquecimento" até re-ingestão completa.

### C.3 Gestão dos Dados

| Acção | Comando |
|---|---|
| Ver ficheiros ingeridos | `ls -la dados/PDF/academicos/` |
| Ver uploads via WhatsApp | `ls -la dados/uploads/` |
| Ver manifesto de ingestão | `cat dados/.ingest_manifest.json` |
| Forçar re-ingestão | `docker exec bot python -c "from src.rag.ingestion import Ingestor; Ingestor().ingerir_tudo()"` |

### C.4 Comandos de Diagnóstico Rápido

```bash
# Verifica saúde completa do sistema
curl http://localhost:9000/health

# Lista sources no Redis
curl http://localhost:9000/banco/sources

# Abre o dashboard de monitoramento
xdg-open http://localhost:9000/monitor

# Verifica fatos de um aluno
curl http://localhost:9000/fatos/5598999999999

# Limpa cache por rota (requer ADMIN_API_KEY)
curl -X DELETE http://localhost:9000/cache/CALENDARIO \
     -H "X-Admin-Key: SUA_ADMIN_API_KEY"
```

---

## Parte D — Perguntas de Teste para o LLM

*Esta secção contém perguntas-alvo para o RAG eval. O LLM deve conseguir responder correctamente usando este documento como contexto.*

### D.1 Perguntas sobre Uso do Bot (Parte A)

| ID | Pergunta | Resposta Correcta |
|---|---|---|
| M01 | "O bot funciona em grupos de WhatsApp?" | Não, apenas em conversas privadas |
| M02 | "Quantas mensagens por hora pode enviar um visitante?" | 50 mensagens por hora |
| M03 | "O bot consegue fazer matrícula de disciplinas?" | Não, deve usar o SIGAA |
| M04 | "Quanto tempo demora a resposta do bot?" | 3 a 10 segundos |

### D.2 Perguntas sobre Comandos Admin (Parte B)

| ID | Pergunta | Resposta Correcta |
|---|---|---|
| M05 | "Como um admin reinicia o AgentCore pelo WhatsApp?" | Enviando `!reload` |
| M06 | "Quanto tempo demora ingerir um PDF de 10 páginas com PyMuPDF?" | ~45 segundos |
| M07 | "O que faz o comando !ragas?" | Exporta logs de produção como dataset de avaliação |
| M08 | "Como o admin ingere um CSV pelo WhatsApp?" | Anexa o ficheiro com legenda `!ingerir` |

### D.3 Perguntas sobre Comandos Linux (Parte C)

| ID | Pergunta | Resposta Correcta |
|---|---|---|
| M09 | "Qual comando apaga todos os dados do Redis?" | `redis-cli FLUSHDB` |
| M10 | "Como acompanhar os logs do Celery worker?" | `docker-compose logs -f worker` |
| M11 | "Como verificar o TTL de um chunk no Redis?" | `redis-cli TTL "rag:chunk:abc123"` |
| M12 | "Como forçar re-ingestão de todos os documentos?" | `docker exec bot python -c "...Ingestor().ingerir_tudo()"` |

---

*Fim do Manual. Versão 1.0 — Para suporte técnico: CTIC/UEMA — ctic@uema.br*