# PGP Oráculo UEMA — Seções IX a 3.3 (bloco consolidado)

> Bloco de trabalho para copiar/colar no Word. Consolida tudo que foi
> fechado no chat desde "Objetivos e Critérios de Sucesso" até "Custo",
> já com os números reais auditados (código + painel `/hub/llm-custo`,
> 01/09/2026).

---

## 0.1 Objetivo deste Documento

Este Plano de Gerenciamento do Projeto estabelece a organização, os
processos e os mecanismos de acompanhamento necessários à execução,
controle, monitoramento e encerramento do projeto Oráculo UEMA. É de
propriedade do projeto Oráculo, mantido pelo Centro de Tecnologia da
Informação e Comunicação (CTIC) da UEMA, e sua revisão está condicionada à
aprovação do responsável técnico do projeto.

## 0.2 Documentos de Referência

| Documento | Status |
|---|---|
| Termo de Abertura do Projeto (TAP) | **Não existe ainda** — recomendado como próximo documento |
| Registro de Decisões Arquiteturais (ADRs) | Existente, uso contínuo |
| Registro de Débito Técnico | Existente (18 itens catalogados) |
| Documentação técnica de arquitetura | Existente, fonte oficial do estado técnico do sistema |
| Log cronológico de sessões de engenharia | Existente, usado como evidência histórica |

## 0.3 Responsabilidades pela Elaboração e Aprovação

| Papel | Responsável | Responsabilidade |
|---|---|---|
| Elaboração | `[seu nome]` | Redação, atualização e controle de versão deste PGP |
| Aprovação | `[definir]` | Validação formal de cada nova versão antes de entrar em vigor |
| Revisão técnica | `[definir]` | Verificação de aderência entre o que o PGP descreve e o estado real do código |

---

## IX. Objetivos e Critérios de Sucesso

### Objetivos Técnicos

| ID | Objetivo | Critério de Sucesso | Status real (01/09/2026) |
|---|---|---|---|
| O1 | Multimodalidade (STT/TTS/Vision) | Testes end-to-end passando no WhatsApp real; latência dentro de SLA por modalidade | STT e TTS concluídos e em produção. Vision: não iniciado, sem data prevista. |
| O2 | Multi-provider LLM com troca dinâmica | Provedores cadastráveis sem redeploy; custo/latência por provedor visível em painel | Concluído — `llm_provider_registry` dinâmico (migration 017); provedores `openai_compat` cadastráveis pelo Hub v2. |
| O3 | Observabilidade | Métricas de latência/erro/custo; alertas configurados; SLO formal medido | Prometheus + `alert_rules.yml` presentes; painel de custo/saúde/busca no Hub v2 confirmado em uso real (ver §3.3). Dashboards Grafana não confirmados no repositório. SLO/P95 sob carga real ainda não medidos. |
| O4 | RBAC testado | Testes de acesso negado cobrindo domínio de permissões | Confirmado: 57 testes em `test_rbac.py` + `test_permissions.py`, suíte estável. |
| O5 | Reduzir débito técnico | Débito catalogado com dono/evidência; itens críticos tratados | 18 itens catalogados (TD-001 a TD-018) em registro formal, cada um com evidência e prioridade. |
| O6 | Hub administrativo como centro de controle operacional | Ferramentas/canais/provedores configuráveis sem código; piloto de execução visual de fluxo | Concluído (Sprints 0–8). Pendente: sign-off visual final. |

### Critérios de Aceitação Globais

- **674 testes coletados** em `tests/unit` (confirmado 01/09/2026) — **652 passando, 3 falhando, 19 pulados**. As 3 falhas restantes **não são bugs de produção** — são débito técnico de teste já catalogado (**TD-017**: teste desatualizado após melhoria de segurança no fluxo SIGAA; **TD-018**: 2 testes sensíveis à flag de ambiente `DEV_TEST_NO_DB_WRITE`) — nenhuma alteração pode reduzir o total de 674 sem justificativa registrada;
- Nenhum `ERROR` não tratado em logs de produção fora dos retries esperados de provedor de LLM;
- Merge na branch principal apenas após: code review + suíte de testes verde (ou falha justificada por item de débito técnico já catalogado) + verificação visual do item alterado.

---

## X. Premissas do Projeto

| ID | Premissa | Impacto se deixar de ser verdadeira |
|---|---|---|
| P1 | Infraestrutura Docker Compose (API + múltiplos workers Celery) permanece operacional | Alto — falha de orquestração interrompe o pipeline inteiro |
| P2 | Redis Stack (RediSearch + RedisVL) mantém índices sem perda de dados | Alto — busca RAG e roteamento semântico dependem da integridade dos índices |
| P3 | Quota de API dos provedores de LLM suficiente para o volume real de uso | Médio — esgotamento de quota interrompe resposta; disjuntor/fallback entre provedores mitiga parcialmente |
| P4 | Cadeia de migrations Alembic permanece íntegra (sem heads divergentes) | Alto — qualquer *gap* invalida deploy em ambiente novo; cadeia atual vai até a migration 019 |
| P5 | Chaves de API de provedores externos permanecem apenas em variável de ambiente, nunca no banco | Médio — regressão disso é risco de vazamento de credencial |
| P6 | Equipe técnica mantém acesso operacional ao ambiente para diagnóstico | Médio — sem isso, qualquer incidente de produção não tem resposta rápida |

---

## XI. Restrições do Projeto

| ID | Restrição | Mitigação |
|---|---|---|
| R1 | Hardware sem GPU dedicada para os workers de mídia | Vision (se implementado) depende de API externa; TTS local (Kokoro) já roda em CPU |
| R2 | Dependências experimentais na mesma imagem Docker de produção (TD-005) | Risco de build aceito por ora; separação de imagens é recomendação em aberto |
| R3 | Sem S3/CDN para mídia — envio direto em base64 | Funciona para o volume atual; reavaliar se volume de mídia crescer |
| R4 | Gateway de mensageria não é API oficial do WhatsApp | Fora do controle do projeto; deduplicação por identificador de mensagem já implementada |
| R5 *(RESOLVIDA)* | LangGraph como dispatcher de produção — decisão já fechada | Não é mais restrição; registro histórico |
| R6 | Dois caminhos de execução de mensagem coexistindo (dispatcher legado + LangGraph) — TD-001 | Convivência documentada e monitorada; aposentadoria de um dos dois é decisão arquitetural pendente |
| R7 | Equipe técnica de porte reduzido, sem função de PM dedicada até este documento | Este próprio PGP é a mitigação |

---

## XII. Estrutura Analítica do Projeto (EAP)

```
Oráculo UEMA
├── 1. Núcleo Conversacional e IA
│   ├── 1.1 Roteamento e Classificação (Supervisor, 5 camadas)
│   ├── 1.2 RAG e Base de Conhecimento Institucional
│   ├── 1.3 Multimodalidade (STT/TTS implementados; Vision não iniciado)
│   └── 1.4 Camada Multi-Provider de LLM (seleção dinâmica via painel)
├── 2. Módulos de Serviço Especializados
│   ├── 2.1 SIGAA (elegibilidade + autenticação supervisionada)
│   ├── 2.2 Chamados/Tickets (RBAC + CRUD)
│   └── 2.3 Cadastro e Conversação (funil de onboarding)
├── 3. Hub Administrativo v2 — Centro de Controle Operacional
│   ├── 3.1 Fundação (componentes compartilhados, glossário central)
│   ├── 3.2 Rotas/Agentes/Capabilities + ferramentas dinâmicas
│   ├── 3.3 Configuração — provedores de LLM dinâmicos
│   ├── 3.4 Canais de comunicação dinâmicos
│   ├── 3.5 Componentes de grafo + registro de servidores MCP
│   ├── 3.6 Infraestrutura — Armazenamento & Cache
│   ├── 3.7 Infraestrutura — Busca & Índices
│   ├── 3.8 Custo & Saúde do sistema
│   └── 3.9 Execução visual de fluxo (GraphExecutor, piloto)
├── 4. Qualidade, Débito Técnico e Observabilidade
│   ├── 4.1 Registro e gestão de débito técnico
│   ├── 4.2 Suíte de testes automatizados
│   └── 4.3 Métricas, alertas e painéis de saúde
└── 5. Governança do Projeto
    ├── 5.1 Plano de Gerenciamento do Projeto (este documento)
    ├── 5.2 Registro de decisões arquiteturais (ADRs)
    └── 5.3 Controle de versões e linha de base de escopo
```

## XIII. Dicionário EAP

| ID | Pacote de Trabalho | Descrição | Status (01/09/2026) |
|---|---|---|---|
| 1.1 | Roteamento e Classificação | Supervisor de 5 camadas | Implementado |
| 1.2 | RAG e Base de Conhecimento | Busca híbrida sobre índice vetorial institucional | Implementado; migração de novos campos de taxonomia pendente |
| 1.3 | Multimodalidade | Transcrição (STT) e síntese (TTS) de voz | STT/TTS implementados; Vision planejado, não iniciado |
| 1.4 | Multi-Provider LLM | Registro dinâmico de provedores, seleção via painel | Implementado |
| 2.1 | SIGAA | Elegibilidade + fluxo de autenticação HITL | Implementado |
| 2.2 | Tickets | Abertura/consulta de chamado com RBAC | Implementado e testado (57 testes) |
| 2.3 | Cadastro/Conversação | Funil de onboarding conversacional | Implementado |
| 3.1–3.9 | Hub v2 | Centro de controle operacional | Implementado; falta sign-off visual final |
| 4.1 | Débito Técnico | Registro formal de 18 itens (TD-001 a TD-018) | Catalogado; não resolvido (por decisão de escopo) |
| 4.2 | Testes Automatizados | Suíte unitária | 674 coletados / 652 passando / 3 débito técnico de teste |
| 4.3 | Observabilidade | Prometheus + regras de alerta + painéis no Hub | Implementado; confirmado em uso real (painel de custo, ver §3.3) |
| 5.1–5.3 | Governança | PGP, ADRs, controle de versão | Em elaboração (este documento) |

---

## 3. Organização do Projeto e Matriz de Responsabilidade

> ⚠️ Esta seção é organizacional, não vem de auditoria de código — confirme
> os papéis reais do CTIC/UEMA antes de fechar.

### Registro de Partes Interessadas

| ID | Parte Interessada / Papel | Interesse | Influência | Classificação | Engajamento |
|---|---|---|---|---|---|
| PS-01 | Responsável Técnico do Projeto (você) | Alto | Alto | Crítico | Contínuo |
| PS-02 | CTIC — Direção/Coordenação | Alto | Alto | Crítico | Marcos e aprovações |
| PS-03 | `[definir]` Orientador acadêmico (se aplicável) | Médio | Médio | Importante | Revisão periódica |
| PS-04 | Coordenação SIGAA / setores acadêmicos integrados | Médio | Médio | Importante | Sob demanda (integrações) |
| PS-05 | Estudantes e servidores (usuários finais) | Alto | Baixo | Crítico | Uso contínuo + feedback |
| PS-06 | `[definir]` Equipe de suporte/help desk (se existir) | Baixo | Baixo | Informado | Pós-implantação |

### Estratégia de Engajamento

| Parte Interessada | Comunicação | Frequência | Objetivo |
|---|---|---|---|
| PS-01 | Documentação de projeto (este PGP), commits, ADRs | Contínua | Registrar decisão e progresso |
| PS-02 | Relatório de status / apresentação de marco | `[definir]` | Aprovar escopo, orçamento e mudanças relevantes |
| PS-03 | Reunião de orientação | `[definir]` | Validar aderência acadêmica/metodológica do projeto |
| PS-04 | Comunicação direta | Sob demanda | Alinhar mudanças que afetem integrações acadêmicas |
| PS-05 | Canal WhatsApp + eventual formulário de feedback | Pós-implantação de cada entrega | Coletar percepção de uso real |

### Estrutura Organizacional do Projeto

```
                    Responsável Técnico do Projeto
                          (PS-01 — você)
                                │
        ┌───────────────┬──────┴──────┬────────────────┐
        │               │             │                │
   Desenvolvimento   Infraestrutura  Governança      Integrações
   (núcleo IA,       e Operação      (PGP, ADRs,     Acadêmicas
   Hub, agentes)     (Docker,        débito técnico)  (SIGAA, wiki
        │             Redis, DB)          │           CTIC)
        │                │                │                │
   [você /          [você /          [você]          Coordenação
   `definir`]        `definir`]                       SIGAA (PS-04,
                                                        sob demanda)
```

### Matriz de Responsabilidades (RACI) — por pacote de trabalho da EAP

| Pacote de Trabalho | Responsável (R) | Aprovador (A) | Consultado (C) | Informado (I) |
|---|---|---|---|---|
| 1.x Núcleo Conversacional e IA | `[você]` | `[você]` | — | PS-02 (CTIC) |
| 2.x Módulos de Serviço Especializados | `[você]` | `[você]` | PS-04 (Coord. SIGAA) | PS-02 |
| 3.x Hub Administrativo v2 | `[você]` | `[você]` | — | PS-02 |
| 4.x Qualidade e Débito Técnico | `[você]` | `[você]` | — | — |
| 5.x Governança (este PGP, ADRs) | `[você]` | `[definir]` | — | PS-02 |

---

## 3.2 Marcos do Projeto (Cronograma retroativo + próximos alvos)

| Marco | Data | Descrição |
|---|---|---|
| M1 — Início formal do desenvolvimento | 21/08/2026 | Projeto Oráculo iniciado e em desenvolvimento ativo |
| M2 — Núcleo de orquestração de produção consolidado | 25/08/2026 | LangGraph adotado como mecanismo de execução de produção; integração de REST/MCP; RBAC coberto por suíte de testes dedicada |
| M3 — Débito técnico formalmente registrado | 25–26/08/2026 | Auditoria completa do sistema; débito técnico catalogado com evidência e prioridade; observabilidade avançada |
| M4 — Painel administrativo evolui para centro de controle operacional | 31/08/2026 | Configuração dinâmica de ferramentas, canais e provedores de IA pelo painel, sem alteração de código; módulos de infraestrutura observável adicionados; piloto de execução visual de fluxo |
| M5 — Governança formal do projeto instituída | 01/09/2026 | Este Plano de Gerenciamento do Projeto elaborado e adotado |
| M6 — Validação final da entrega mais recente | `[definir]` | Sign-off visual/funcional pendente |
| M7 — Tratamento de débito técnico prioritário | `[definir]` | Correção dos itens de prioridade alta ainda abertos |
| M8 — Suporte a visão computacional | `[definir]` | Não iniciado; sem marco de início definido |

---

## 3.3 Custo

Instantâneo real do painel de custo e provedores (`/hub/llm-custo`), capturado em 01/09/2026:

| Indicador | Valor |
|---|---|
| Provedor de IA ativo | Gemini |
| Cotação do dólar utilizada | R$ 5,18 |
| Mensagens processadas (últimas 24h) | 13 |
| Tokens consumidos (últimas 24h) | 19.263 |
| Custo (últimas 24h) | US$ 0,0067 (R$ 0,03) |
| Latência média | 2.885 ms |
| Taxa de acerto de cache | 0% |

**Custo por provedor cadastrado:**

| Provedor | Status de credencial | Disjuntor (circuit breaker) | Custo (USD) | Custo (BRL) |
|---|---|---|---|---|
| Gemini | Configurado (oficial) | Operacional | US$ 0,0067 | R$ 0,03 |
| DeepSeek | Não configurado (sem credencial) | Operacional | US$ 0,00 | R$ 0,00 |
| Groq | Configurado (oficial) | Operacional | US$ 0,00 | R$ 0,00 |

**Custo por assunto/rota (últimos dias, painel em produção):** o painel já
segrega custo por tipo de pergunta (contatos e setores, classificação de
rota, base de conhecimento/wiki, calendário acadêmico, pergunta geral,
reformulação de consulta), cada um com latência e taxa de cache próprias —
útil para identificar qual tipo de demanda mais pesa no custo total à
medida que o volume crescer.

**Tabela de preços vigente por modelo (USD por 1M tokens):**

| Provedor | Modelo | Entrada (IN) | Saída (OUT) | Cache |
|---|---|---|---|---|
| DeepSeek | deepseek-chat | 0,20 | 1,20 | 0,02 |
| Gemini | gemini-2.5-flash | 0,30 | 2,50 | — |
| Gemini | gemini-2.5-flash-lite | 0,10 | 0,40 | — |

> ⚠️ **Leitura correta destes números:** o volume de 13 mensagens/24h e
> custo de R$ 0,03/dia reflete **uso de desenvolvimento/teste, não uso em
> produção real com a comunidade acadêmica**. Não deve ser lido como
> projeção de custo em escala — é evidência de que o mecanismo de medição
> funciona corretamente, não uma meta ou teto de gasto real. Projetar custo
> para volume de produção (centenas/milhares de estudantes) exige uma
> estimativa separada de mensagens/dia esperadas × custo médio por token
> pelo modelo ativo.

**Teto orçamentário formal:** `[a validar com o patrocinador]` — ainda não
há limite de gasto mensal/diário definido institucionalmente; a
infraestrutura de monitoramento (este mesmo painel) já está pronta para
acompanhar esse teto assim que for definido, incluindo alerta de
ultrapassagem por dia/mês.
