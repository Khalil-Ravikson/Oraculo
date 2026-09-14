# PGP Oráculo UEMA — 4.3 Custo Detalhado, 5-8 Controle do Projeto

---

## 4.3.1 Consumo Observado — Série de Medições Reais

> Duas medições reais existem até agora, em datas diferentes — apresentadas
> como série, não como número único, para já registrar tendência:

| Métrica | Medição 21/08/2026 | Medição 01/09/2026 |
|---|---|---|
| Mensagens processadas (24h) | 15 | 13 |
| Usuários únicos | 3 | `[não capturado nesta medição]` |
| Tokens totais | 18.686 | 19.263 |
| Tokens de entrada | 18.208 | `[não segregado nesta medição]` |
| Tokens de saída | 478 | `[não segregado nesta medição]` |
| Custo (USD) | US$ 0,006657 | US$ 0,0067 |
| Custo (BRL) | R$ 0,0346 | R$ 0,03 |
| Custo por mensagem | US$ 0,000444 | US$ 0,000515 |
| Latência média | 2.050 ms | 2.885 ms |
| Cache hit | 0% | 0% |

**Leitura:** custo por mensagem e volume seguem na mesma ordem de grandeza
entre as duas medições (11 dias de diferença) — consistente com uso ainda
em fase de desenvolvimento/teste, não produção real. Cache hit em 0% nas
duas medições é esperado neste volume (poucas perguntas repetidas o
suficiente pra bater no cache semântico).

---

## 4.3.2 Distribuição de Custos por Rota (medição de 21/08/2026)

| Rota | Chamadas | Custo (USD) | Custo (BRL) | % do Total | Latência Média |
|---|---|---|---|---|---|
| router_classify | 7 | US$ 0,003156 | R$ 0,0164 | 47,4% | 2.318 ms |
| GERAL (RAG) | 2 | US$ 0,001588 | R$ 0,0082 | 23,8% | 2.703 ms |
| WIKI (CTIC + RAG) | 2 | US$ 0,001480 | R$ 0,0077 | 22,2% | 2.546 ms |
| query_transform | 4 | US$ 0,000434 | R$ 0,0023 | 6,5% | 1.006 ms |

**Leitura relevante para decisão de custo:** quase metade do custo
(47,4%) vem da **classificação de rota** (`router_classify`), não da
geração da resposta final — ou seja, otimizar essa etapa (mais regras
determinísticas no Supervisor antes do fallback LLM, ou usar Groq/DeepSeek
especificamente aqui, que são mais baratos que o Gemini) tem retorno
proporcionalmente maior que otimizar a síntese de resposta.

---

## 4.3.3 Cache Semântico (medição de 21/08/2026)

| Rota | Entradas Ativas | TTL | Threshold de Similaridade |
|---|---|---|---|
| `semcache:CONTATOS:*` | 1 | 2.880 min (48h) | 0,93 |
| `semcache:WIKI:*` | 1 | 720 min (12h) | 0,88 |
| `semcache:GERAL:*` | 2 | 30 min | 0,90 |

---

## 4.3.4 Projeção de Custos por Cenário de Uso

> Projeção linear a partir do custo real por mensagem (US$ 0,000444,
> medição de 21/08). É extrapolação simples — não considera economia de
> escala (cache hit deve subir com mais usuários repetindo pergunta), nem
> troca de provedor por um mais barato (Groq/DeepSeek, ver análise
> comparativa em arquivo separado).

| Cenário | Usuários/dia | Mensagens/dia | Custo/dia (USD) | Custo/mês (USD) | Custo/ano (BRL) |
|---|---|---|---|---|---|
| Desenvolvimento atual | 3 | 15 | US$ 0,0067 | US$ 0,20 | R$ 12,45 |
| Baixo — teste | 20 | 100 | US$ 0,044 | US$ 1,32 | R$ 82 |
| Médio | 100 | 500 | US$ 0,22 | US$ 6,60 | R$ 408 |
| Alto | 300 | 1.500 | US$ 0,67 | US$ 19,80 | R$ 1.232 |
| Máximo — 10% da UEMA | 1.000 | 5.000 | US$ 2,22 | US$ 66 | R$ 4.106 |

**Leitura:** mesmo no cenário máximo projetado (10% da comunidade UEMA
usando diariamente), o custo de LLM fica abaixo de R$ 350/mês — o custo de
tokens **não é o fator limitante** deste projeto; hospedagem/infraestrutura
tende a pesar mais (ver análise comparativa de hospedagem).

---

## 4.3.5 Orçamento Operacional Revisado

| Item | Desenvolvimento (20 semanas) | Produção (Ano 1) | Total |
|---|---|---|---|
| APIs Gemini + DeepSeek + Groq + Brave | R$ 1.738 | R$ 838 | R$ 2.576 |
| VM Staging — AWS t3.medium | R$ 705 | — | R$ 705 |
| Backup / S3 | R$ 235 | R$ 240 | R$ 475 |
| Redis / PostgreSQL — tier gratuito | R$ 0 | R$ 0 | R$ 0 |
| **Subtotal** | **R$ 2.678** | **R$ 1.078** | **R$ 3.756** |
| Contingência — 30% | R$ 803 | R$ 323 | R$ 1.127 |
| **TOTAL** | **R$ 3.481** | **R$ 1.401** | **R$ 4.883** |

> ⚠️ **Atenção a uma inconsistência real com o resto do documento**: esta
> tabela assume uma **VM de staging na AWS (t3.medium, R$ 705)** — mas você
> confirmou que hoje o Docker roda na sua máquina local, e cogitou migrar
> para o servidor da CTIC (custo ≈ zero). Se a AWS não estiver
> efetivamente contratada, esses R$ 705 de "Desenvolvimento" não são custo
> real — são uma estimativa de um cenário que talvez nem aconteça (dado
> que CTIC é a opção de menor custo). Recomendo marcar essa linha como
> **cenário alternativo**, não como orçamento comprometido, até a decisão
> AWS vs. CTIC ser tomada.

---

## 4.3.6 Reserva de Contingência

Foi considerada uma reserva de contingência de 30% sobre os custos
operacionais estimados, absorvendo variações de consumo de APIs,
alterações de preços ou infraestrutura adicional não prevista. O valor
estimado de contingência é de R$ 1.127, resultando em um orçamento
operacional total estimado de **R$ 4.883**.

---

## 4.3.7 Premissas e Limitações do Orçamento

- Os custos de API são baseados no padrão de consumo observado durante o
  desenvolvimento (medições de 21/08 e 01/09/2026 — ver 4.3.1);
- O volume de utilização em produção poderá alterar significativamente o
  custo final — a projeção da 4.3.4 é linear e não considera economia por
  cache nem troca de provedor;
- Os valores de infraestrutura (item AWS) podem não se aplicar caso a
  migração para o servidor da CTIC seja aprovada — ver ressalva em 4.3.5;
- Redis e PostgreSQL foram considerados dentro de planos/tiers gratuitos
  na estimativa atual;
- **O custo de pessoal não está incluído neste orçamento** e deve ser
  calculado separadamente, considerando as taxas horárias vigentes na
  organização, caso a equipe deixe de ser voluntária/acadêmica.

---

## 5. Como Será Medido o Progresso do Projeto

Progresso medido por Gerenciamento do Valor Agregado (EVM), com dois
indicadores e semáforo de status:

| Indicador | Verde | Amarelo | Vermelho |
|---|---|---|---|
| IDP (Índice de Desempenho de Prazo) | ≥ 1,0 | ≥ 0,9 e < 1,0 | < 0,9 |
| IDC (Índice de Desempenho de Custo) | ≥ 1,0 | ≥ 0,9 e < 1,0 | < 0,9 |

- **IDP** = VA / VP — eficiência de cronograma (Valor Agregado sobre Valor Planejado).
- **IDC** = VA / CR — eficiência de custo (Valor Agregado sobre Custo Real).

A linha de base de prazo e custo é salva ao final do planejamento; o
acompanhamento entre planejado (linha de base) e realizado é feito
semanalmente.

> ⚠️ **Pré-requisito não atendido ainda**: EVM exige uma linha de base de
> **custo e cronograma por pacote de trabalho** — que este PGP ainda não
> tem em granularidade suficiente (a EAP tem status, mas não valor
> planejado por pacote). Sem isso, IDP/IDC não têm o que calcular. Registrar
> como ação pendente antes desta seção virar operacional de fato.

---

## 6. Gestão de Riscos e Problemas

Matriz construída a partir do registro de débito técnico (TD-001 a TD-018,
todos com evidência em `docs/technical-debt.md`) e das restrições do
projeto (seção XI) — cada linha é um risco real e rastreável, não uma
estimativa genérica. Onde uma Restrição já duplicava um TD (mesma causa
raiz), mantive uma linha só e referenciei a outra, para não inflar a
contagem com o mesmo risco duas vezes.

### 6.1 Mapa de Calor (Severidade × Urgência)

| | **Urgência Alta** | **Urgência Média** | **Urgência Baixa** |
|---|---|---|---|
| **Severidade Alta** | 🔴 3 itens (TD-009, TD-010, TD-013) | 🟠 2 itens (TD-001, TD-002) | — |
| **Severidade Média** | 🟠 1 item (TD-018) | 🟡 3 itens (TD-005, TD-006, R4) | 🟡 4 itens (TD-003, TD-011, R1, R7) |
| **Severidade Baixa** | — | — | 🟢 9 itens (TD-004, TD-007, TD-008, TD-012, TD-014, TD-015, TD-016, TD-017, R3) |

**Leitura executiva:** 3 riscos em zona crítica (Sev. Alta + Urg. Alta) —
todos técnicos, nenhum de escopo/prazo/orçamento. Nenhum risco de negócio
ou institucional está na zona vermelha hoje.

### 6.2 Registro Detalhado

| ID | Risco | Severidade | Urgência | Impacto | Ação |
|---|---|---|---|---|---|
| TD-009 | OOM não testado em `worker_media` sob carga real de STT/TTS | Alta | Alta | Indisponibilidade do worker em produção sob uso real de voz | Testar sob carga e ajustar `mem_limit` antes de expor STT/TTS a volume real |
| TD-010 | `GEMINI_MODEL` aponta para versão *preview*, sujeita a erro 404 | Alta | Alta | Risco operacional ativo — falha de resposta a qualquer momento | Fixar versão estável do modelo (ação de infraestrutura/produção) |
| TD-013 | Gatekeeper: toda decisão `IGNORE` é reescrita para `LLM` | Alta | Alta | Filtros de segurança de entrada (grupo estranho, texto vazio, comando admin indevido) não bloqueiam nada hoje | Avaliação de segurança/produto dedicada — por que o override existe e o que quebra sem ele |
| TD-001 | Dois orquestradores de mensagem coexistindo (`dispatcher.py` + `dispatcher_langgraph.py`) | Alta | Média | Mudança de roteamento precisa ser verificada em dois lugares; já causou bug real em produção | Decisão arquitetural: aposentar `dispatcher.py` ou formalizar coexistência permanente |
| TD-002 | Fast-Path em `dispatcher.py` contorna o Planner | Alta | Média | Três lugares de decisão de rota, sem fonte única de verdade | Resolver junto com TD-001 |
| TD-018 | Testes de cadastro dependem da flag `DEV_TEST_NO_DB_WRITE` do ambiente | Média | Alta | Suíte de testes não-determinística — falso-negativo imediato para quem rodar localmente | Testes devem forçar a flag via monkeypatch, independente do ambiente |
| TD-005 | Dependências experimentais na imagem de produção (`langgraph`, `mcp`, `kokoro`) | Média | Média | Risco de quebra de build ao mesclar; imagem maior que o necessário | Separar imagens ou mover dependências para grupo opcional |
| TD-006 | Cobertura de testes zero em `memory/`, `rag/`, `services/` | Média | Média | Mudanças nesses módulos sem rede de segurança automatizada | Aumentar cobertura (trabalho de engenharia dedicado) |
| R4 | Gateway de mensageria (Evolution API) não é API oficial do WhatsApp | Média | Média | Fora do controle do projeto quanto a disponibilidade/mudança de comportamento | Deduplicação por `msg_key_id` já implementada; monitorar estabilidade |
| TD-003 | Migração `services/` → `capabilities/` incompleta | Média | Baixa | Duas estruturas paralelas para o mesmo tipo de responsabilidade — confuso para quem aprende o projeto | Mover código ativo quando houver refatoração dedicada |
| TD-011 | Migration `004_recria_tabela_pessoas` sem explicação no histórico Alembic | Média | Baixa | Possível perda de dados histórica não confirmável só pela leitura do código | Validação histórica com quem operou o banco antes dessa migration |
| R1 | Hardware sem GPU dedicada para workers de mídia | Média | Baixa | Geração de imagem local descartada; Vision (se implementado) depende de API externa | Manter dependência de API externa para tarefas que exigem GPU |
| R7 | Equipe técnica reduzida, sem função de PM dedicada até este documento | Média | Baixa | Risco de continuidade de manutenção e evolução | Este próprio PGP é a mitigação — formaliza processo sem exigir crescimento de equipe |
| TD-004 | `pyproject.TOML` desatualizado, não lido por build/CI | Baixa | Baixa | Confunde quem tenta reproduzir o ambiente a partir dele | Decidir unificar com `requirements.txt` ou remover |
| TD-007 | Import quebrado em `rag/query_transform.py` (módulo antigo, fora do hot path) | Baixa | Baixa | `ModuleNotFoundError` se alguém exercitar esse caminho específico | Corrigir import ou remover módulo antigo |
| TD-008 | Redis diferente entre CI (`redis:7-alpine`) e produção (`redis-stack`) | Baixa | Baixa | Teste com comando `FT.*`/`JSON.*` pode passar local e falhar no CI, ou vice-versa | Alinhar imagem Redis usada no CI com a de produção |
| TD-012 | `tests/test_wiki_scraper.py` órfão (import quebrado) | Baixa | Baixa | Quebra com `ImportError` se executado sem escopo | Atualizar ou remover o arquivo de teste |
| TD-014 | 4 arquivos em `tests/e2e/` órfãos (import quebrado) | Baixa | Baixa | Mesma classe de risco do TD-012, escopo maior (4 arquivos) | Atualizar ou remover |
| TD-015 | `RedisVLVectorAdapter.buscar_hibrido` emite comando não suportado pelo Redis Stack em uso | Baixa | Baixa | Código morto hoje (hot path usa o caminho sync); risco médio só se alguém ligar o adapter async | Subir versão do módulo Redis Stack ou reescrever para o padrão sync |
| TD-016 | Disjuntor de LLM (`llm_circuit_breaker`) ignora provedores dinâmicos cadastrados pelo Hub v2 | Baixa | Baixa | Visão agregada de saúde incompleta; o disjuntor em si funciona por trás | Trocar tupla hardcoded por `llm_provider_registry.registrados()` |
| TD-017 | Teste do fluxo SIGAA desatualizado após melhoria de segurança (senha não vai mais em texto plano) | Baixa | Baixa | Falso-negativo na suíte — corrói confiança, não indica bug real | Reescrever asserção para resolver senha via `auth_token` |
| R3 | Sem S3/CDN para mídia — envio direto em base64, limite de 16MB | Baixa | Baixa | Limita tamanho de mídia enviável hoje | Reavaliar se volume de mídia crescer |

*(Restrições R2 e R6 não geraram linha própria — R2 é a mesma causa raiz do TD-005, e R6 é a mesma do TD-001.)*

### 6.3 Risco Operacional Ativo (registro pontual já existente)

Nenhum achado das fases de integração LangGraph/REST/MCP ficou classificado
como crítico. O único risco em aberto por decisão consciente, fora da
matriz de débito técnico, é a ausência de teste manual via WhatsApp real
das rotas nativas do LangGraph antes de ativar as flags em produção —
mitigado pelo fato de as flags permanecerem desligadas por padrão na
branch principal.

---

## 7. Gestão da Comunicação

> ⚠️ Mesma situação da seção 6 — o conteúdo recebido era só a estrutura de
> template (colunas "Importância", "Quando?", "Onde?", "Urgência",
> "Impacto", "Comentários"), sem linhas preenchidas.

Isso já está parcialmente coberto pela **Estratégia de Engajamento** da
seção "Organização do Projeto" (registro de stakeholders + comunicação por
PS-01 a PS-06) — recomendo usar aquela tabela como base e só adicionar as
colunas que faltam (Urgência × Impacto, Onde é armazenado) em vez de
recriar do zero.

---

## 8. Gestão de Mudança de Escopo

Toda mudança de escopo deve ser solicitada por formulário, avaliada pelo
responsável do projeto, registrada em log de mudanças (mantido na pasta do
projeto, com todas as solicitações e status — inclusive as rejeitadas) e
encaminhada para aprovação do patrocinador antes de entrar em vigor.

**Exemplo real de mudança de escopo já processada por este fluxo:** a
promoção do LangGraph a dispatcher definitivo de produção e a
formalização de REST/MCP como funcionalidades constituíram uma mudança de
escopo real em relação ao planejamento original (que tratava o LangGraph
como "em reavaliação"). A decisão foi registrada formalmente antes da
execução e documentada em ADRs versionados no repositório
(`docs/decisions/`) — este é o padrão de evidência esperado para toda
futura mudança de escopo deste projeto.
