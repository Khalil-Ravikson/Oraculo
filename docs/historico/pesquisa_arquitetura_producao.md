# pesquisa_arquitetura_producao.md

> **Status: 🗄️ histórico — parcialmente superado.** Movido para
> `docs/historico/` em 2026-08-25. A tabela da §3 ("zero CI/CD hoje", "nenhuma
> telemetria de custo instalada") ficou desatualizada uma sessão depois de
> escrita — `.github/workflows/tests.yml` existe e a telemetria de custo foi
> conectada (ver `notas.md` §13). O item ainda genuinamente em aberto: RBAC
> completo testado em `main` continua bloqueando a promoção do LangGraph (ver
> `notas.md`, última entrada). Mantido como registro do raciocínio original.

> Rascunho de discussão, no mesmo espírito de `notas_regras_negocio_chunkviz.md`:
> nada aqui é decisão arquitetural fechada. É a base pra discutirmos os
> próximos passos de maturidade de produção do Oráculo.

## 1. Objetivo do documento

Em 2026-08-14 foi feita uma pesquisa técnica profunda ao Perplexity pedindo
uma arquitetura completa de produção (orquestração de agentes, pipeline de
dados, CI/CD, evals, governança, observabilidade, MCP, economia de tokens)
pro Oráculo UEMA. A resposta é tecnicamente sólida *em abstrato*, mas foi
escrita sem acesso ao código real — assume um nível de maturidade (CI/CD,
observabilidade LLM dedicada, governança formalizada) que não bate com o que
está documentado em `.claude.md`, `arquitetura_oraculo.md`, `notas.md` e
`notas_regras_negocio_chunkviz.md`.

Este documento cruza os dois lados: o que a pesquisa recomendou vs o que
existe de fato no projeto (confirmado por leitura de código e grep, não só
pelas notas). Serve pra decidirmos, com critério, o que vale adotar agora,
o que é prematuro, e em que ordem.

---

## 2. O que a pesquisa validou (não precisa reconstruir)

A arquitetura real do Oráculo já bate com boa parte do que o Perplexity
recomendou como "moderno" — sinal de que decisões anteriores do projeto
estavam no caminho certo, não que falta construir do zero:

- **RAG híbrido + rerank** — BM25 + HNSW (RedisVL, 3072d) + RRF +
  cross-encoder local já implementado (`arquitetura_oraculo.md` §8). É
  exatamente o pipeline que a pesquisa recomenda como "produção-ready".
- **Semantic caching** — já existe no fluxo real (`SemanticCache (cosine >
  0.92)` em `dispatcher.processar()`, `arquitetura_oraculo.md` §5). A
  pesquisa aponta isso como a estratégia de **maior impacto/menor esforço**
  pra economia de tokens; já está feito, não é um "próximo passo".
- **Model routing implícito** — Flash pra classificação/roteamento/extração
  de fatos, Pro pra planner/síntese (`arquitetura_oraculo.md` §4.3). Já é
  roteamento small/large model por componente, só não está formalizado como
  política explícita nem medido separadamente.
- **Router → Agents → Capabilities** — a arquitetura de 3 camadas já É o
  "quando usar workflow vs router vs agente" que a pesquisa tenta ensinar em
  abstrato.
- **Memória em 5 camadas (L1–L5)** — mais sofisticada do que o conceito
  genérico "short/long-term memory" da pesquisa; já implementada
  (`arquitetura_oraculo.md` §2).
- **Ceticismo sobre MCP confirmado na prática** — a pesquisa avisa "MCP pode
  não reduzir tokens de verdade, cuidado com tool sprawl". O laboratório
  `mcp_lab/` bateu nisso: `list_tools()` do gateway pipeworx devolve um
  catálogo grande de tools "de plataforma" junto com as do pacote anunciado,
  inflando contexto sem escolha (`.claude.md` linha 24). A cautela da
  pesquisa é validada por evidência de primeira mão, não só teoria.
- **Golden dataset embrionário** — `tests/eval/test_ctic_wiki_eval.py` +
  fixtures congeladas (`tests/fixtures/ctic_wiki/*.txt`) é literalmente o
  embrião de um golden dataset — só não roda em CI nem usa framework tipo
  Ragas/DeepEval.

---

## 3. Correções de realidade — onde a pesquisa assume maturidade que não existe

| Tema na pesquisa | O que a pesquisa assume | Realidade confirmada (arquivo/seção) |
|---|---|---|
| CI/CD para IA (fases com canary/rollback) | Pipeline de testes/evals/canary já rodando ou fácil de plugar | **Zero CI/CD hoje** — sem `.github/workflows` no repo (confirmado por `ls`). Testes existem (`tests/unit`, `e2e`, `eval`, `integration`) mas rodam só manualmente. |
| Observabilidade LLM (Langfuse/LangSmith/Phoenix) | Só "escolher a ferramenta certa" | **Nenhuma instalada** — `requirements.txt` só tem `prometheus-fastapi-instrumentator`/`prometheus-client` (confirmado por grep). O que existe é Prometheus genérico + tabelas Postgres (`metricas_llm`, `audit_log`, `feedback_avaliacoes`, `monitor_logs`, `arquitetura_oraculo.md` §6.3) — sem tracing distribuído, sem OpenTelemetry. |
| Avaliação (Ragas/DeepEval) | "Integrar ao CI" | Sem framework de eval instalado; o que existe é pytest cru comparando fixtures — mais raso, mas é base real, não zero. |
| Governança formal (aprovação Junior/Senior/Principal, Risk Committee) | Estrutura corporativa madura | **Nada formalizado** — sem política de uso, sem workflow de aprovação, sem versionamento de prompt fora do editor do `/hub`. Modelo superdimensionado pra um projeto de ~1 dev numa universidade. |
| RBAC/ABAC "antes do retrieval" | Como se fosse plugar uma lib | RBAC real é **dict fixo no código** (`domain/permissions.py`, 247 linhas, `ContextoPermissao`/`_PERMISSOES`) — não editável, não versionado, só recentemente (branch LangGraph) começou a ser checado nos nodes de entrada (`.claude.md` linha 14, item "9.2"). Retrieval hoje filtra por **taxonomia de conteúdo** (`setor`/`campus`/`eixo`), não por permissão do usuário — são coisas diferentes; a pesquisa mistura os dois (ver §5.3 abaixo pra separar isso de verdade). |
| LangGraph como peça do diagrama final | Componente natural da stack de produção | **Isolado numa branch/worktree própria, explicitamente NÃO aprovado pra `main`** (`.claude.md` linha 11). Bloqueios técnicos originais parecem resolvidos (`.claude.md` linhas 14-16), mas falta RBAC testado na `main` antes de sequer reabrir essa conversa — decisão pendente, não fato consumado. |
| MCP como peça do diagrama de produção | Componente já integrado | `mcp_lab/` é laboratório de estudo/prova de capacidade pra apresentar na CETIC/UEMA, roteado por regex (não LLM), deliberadamente **não integrado ao `src/` do núcleo** (`notas.md` §10). |
| Function calling / tool calling nativo | Assume que "controlar ferramentas expostas ao modelo" já é vivido em produção | **Não existe nenhum function-calling agentic em produção hoje.** O padrão real é saída estruturada forçada (`response_schema` Pydantic via `google.genai`). O único código que tentou `bind_tools` do LangChain (`gmail_tool.py`) é código morto, sem consumidor (`notas.md` §10.1, confirmado por grep). Isso muda a resposta prática de "quando usar MCP/tool calling": o Oráculo ainda nem decidiu *adotar* tool-calling real — é um passo anterior ao que a pesquisa assume. |
| Logs correlacionados / dashboards prontos | Assume que dá pra simplesmente montar dashboard | Já é dor viva sem solução: correlacionar `plan_id` entre containers hoje é grep manual (`notas.md` §5.4). Proposta de Loki+Promtail já registrada, não implementada. |
| "Router unificado" como conceito abstrato | Ensina o padrão router/supervisor como algo a adotar | O Oráculo já tem **3 classificadores LLM brigando sem coordenação** (Orquestrador, Supervisor, Planner) — bug real, já causou pelo menos 2 incidentes de produção (worker fantasma `crud_confirm`, `dag_hint` dessincronizado, `notas.md` §1). Não é "escolher o padrão certo", é consertar uma sobreposição que já existe. |

---

## 4. Onde a pesquisa é realmente útil (aplicada aos gaps reais)

Filtrando o genérico, os pontos que respondem a dores **já registradas no
próprio projeto** (não inventadas pela pesquisa):

### 4.1 OpenTelemetry para correlação de trace

Responde diretamente à dor de `notas.md` §5.4 (correlacionar `plan_id` entre
FastAPI/Celery/Gemini/Redis sem grep manual em containers separados). As
convenções semânticas oficiais de GenAI (`gen_ai.*`) definem atributos
padronizados — `gen_ai.request.model`, `gen_ai.usage.input_tokens`/
`output_tokens`, `gen_ai.operation.name`, `gen_ai.response.finish_reasons` —
próprios pra instrumentar chamadas de LLM/embedding/tool-call como spans,
não só como log de texto. Isso é mais estruturado que Loki+Promtail sozinho
(que só torna log buscável, sem correlação de span/trace nativa).

Fonte oficial: [OpenTelemetry — Semantic Conventions for GenAI spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/),
repositório [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai).

### 4.2 Métricas de roteamento

As métricas que a pesquisa lista pra observabilidade de agente batem quase
1:1 com as já propostas em `notas.md` §5.2:

- `oraculo_router_override_total{orchestrator_action, supervisor_rota}`
- `oraculo_planner_worker_not_found_total{worker}`
- `oraculo_orchestrator_json_parse_failures_total`
- `oraculo_rag_zero_chunks_total{doc_type}`

A pesquisa não trouxe nada novo aqui — confirma que a proposta já registrada
é o padrão correto (contador Prometheus por *tipo de falha/decisão*, não só
latência agregada).

### 4.3 CI/CD com golden dataset versionado

O ganho real não é "adotar Ragas/DeepEval agora" — é **ligar o que já
existe** a um pipeline automático: rodar `pytest tests/unit tests/eval` em
CI a cada PR, nem que seja sem framework de eval dedicado ainda. Framework
de LLM-eval fica pra depois que o hábito de CI existir.

### 4.4 RBAC pré-retrieval, separado de taxonomia de conteúdo

A literatura técnica confirma o padrão certo (não é invenção da pesquisa):
o filtro de autorização deve ser aplicado **antes** da busca vetorial —
"pre-filtering" restringe o espaço de busca a documentos autorizados antes
da comparação de similaridade, em vez de buscar tudo e filtrar depois (que
vaza contagem/existência de documento restrito mesmo quando o conteúdo não
é devolvido). Hoje o Oráculo filtra por *taxonomia de conteúdo*
(`setor`/`campus`/`eixo`) — é o mecanismo técnico certo (TAG filter no
RediSearch), só falta a política de **quem pode ver o quê** vir do RBAC do
usuário (`pessoas.role`/`status`), não só de metadado do documento.

Fontes: [Pinecone — RAG with Access Control](https://www.pinecone.io/learn/rag-access-control/),
[AWS Security Blog — Authorizing access to data with RAG implementations](https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations/).
Nota: existe uma evolução além de RBAC simples (ReBAC — relationship-based
access control, ver [Descope](https://www.descope.com/blog/post/rebac-rag))
para cenários onde permissão depende de relação/hierarquia, não só role
fixa — mencionado aqui só como "existe", fora de escopo pro Oráculo agora
(o RBAC atual nem está aplicado em todos os pontos de entrada ainda).

### 4.5 Governança leve (não o modelo corporativo completo)

Da lista extensa de governança da pesquisa, o que é aplicável agora sem
over-engineering, alinhado ao [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)
como *referência de vocabulário* (não framework a implementar por inteiro —
o AI RMF é voluntário e pensado pra organizações de qualquer porte, mas o
processo de aprovação em camadas da pesquisa é desenhado pra empresa grande):

- Lista de modelos aprovados com versão pinada — já parcialmente feito
  (`langgraph-checkpoint-redis==0.5.1` pinado por instabilidade conhecida),
  mas `GEMINI_MODEL` não é pinado, já causou um 404 real com
  `gemini-3.1-flash-lite-preview` (`notas.md` §9.10).
- Classificação de dado por `access_level` — reaproveitar a taxonomia UEMA
  que já existe (`eixo`/`setor`/`campus`) em vez de criar sistema novo.
- Trilha de auditoria — tabela `audit_log` já existe; falta só decidir
  retenção.

---

## 5. Decisão nos 3 forks já registrados como pendentes

A pesquisa não resolve estes sozinha, mas dá o vocabulário/critério técnico
que faltava pra fechar o que já estava em aberto nas notas do projeto:

1. **LangGraph → `main`? Não ainda.** Os dois bloqueios técnicos
   catastróficos (event loop, resumption de `interrupt()` duplo) parecem
   resolvidos no upstream, testados a fundo (`.claude.md` linhas 14-16). Mas
   falta RBAC testado corretamente na `main` (fora do LangGraph) — isso é
   pré-requisito hard, não preferência. Recomendação: fechar esse item
   isolado primeiro (é sobre a `main`, nem depende do LangGraph), *depois*
   reabrir a conversa de promoção com critério técnico — não "não travou
   mais nos últimos testes".
2. **MCP: continuar como laboratório, não promover a feature de produto
   ainda.** A pesquisa reforça exatamente a razão já documentada
   (`notas.md` §10.1): o Oráculo nem decidiu adotar tool-calling real
   (`google.genai`, não LangChain `bind_tools`) — MCP só faz sentido depois
   dessa decisão anterior, não antes. Adotar MCP em produção antes disso é
   resolver a ferramenta antes do problema.
3. **Unificar os 3 classificadores (Orquestrador + Supervisor + Planner)**:
   a pesquisa dá o argumento técnico que faltava — cada chamada custa
   tokens Gemini e cada uma é superfície de bug de precedência (já
   materializou 2x em produção). Recomendação: tratar como item de médio
   prazo, sessão própria com testes de regressão nos 3 agentes ativos (RAG,
   Cadastro, SIGAA/CR) — como `notas.md` §5.1 já recomendava.

---

## 6. Roadmap ajustado (realista pra ~1 dev, orçamento público)

Reordenado a partir do roadmap genérico de 6 fases da pesquisa, priorizado
pelo que **já é dívida técnica confirmada** em vez de "maturidade LLMOps
genérica":

**Fase 0 — Fechar dívida que já causou incidente**
- RBAC testado na `main` (bloqueia a decisão do LangGraph).
- Pinar `GEMINI_MODEL` (evitar 404 de modelo descontinuado/preview).
- Corrigir `docker-compose.yml` profiles sem default (`COMPOSE_PROFILES`) —
  vai doer mais ainda assim que existir CI/CD.
- Flag `DISABLE_DOCLING` (crash real de `SIGKILL` já observado, `notas.md`
  §8.5/8.6).

**Fase 1 — CI/CD mínimo**
- GitHub Actions rodando `tests/unit` + `tests/eval` a cada PR — zero
  ferramenta nova, só automação do que já roda manualmente.
- Smoke test pós-deploy (health check + 2-3 mensagens reais, estilo
  `run_test.py`, sem WhatsApp).

**Fase 2 — Observabilidade dirigida à dor real**
- As 4 métricas Prometheus de `notas.md` §5.2 — são as que teriam pego os
  bugs reais já documentados.
- Avaliar OpenTelemetry só pro problema concreto de correlação de
  `plan_id` — não adotar Langfuse/Phoenix ainda sem medir se compensa o
  custo de manutenção pra 1 dev.

**Fase 3 — Consertar a arquitetura de roteamento**
- Distinguir "Orquestrador decidiu X" de "Orquestrador falhou, isto é
  fallback de emergência" (`notas.md` §1, fix sugerido mas não aplicado).
- Decidir e executar a unificação Orquestrador+Supervisor (§5.3 acima).

**Fase 4 — Governança leve**
- Documento curto de política de uso + lista de modelos aprovados/pinados +
  taxonomia UEMA como classificação de dado.
- Decidir retenção do `audit_log` já existente.

**Fase 5 — Só depois: reabrir MCP/tool-calling real e LangGraph**
- Decisão de tool-calling nativo via `google.genai` (pré-requisito real pra
  MCP valer a pena em produção).
- Promoção do LangGraph pra `main`, condicionada à Fase 0.

---

## 7. Fontes

- [OpenTelemetry — Semantic Conventions for GenAI spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
- [open-telemetry/semantic-conventions-genai (GitHub)](https://github.com/open-telemetry/semantic-conventions-genai)
- [NIST — AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [NIST — Artificial Intelligence Risk Management Framework (AI RMF 1.0), publicação](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
- [Pinecone — RAG with Access Control](https://www.pinecone.io/learn/rag-access-control/)
- [AWS Security Blog — Authorizing access to data with RAG implementations](https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations/)
- [Descope — Adding Performant ReBAC to RAG Pipelines at Scale](https://www.descope.com/blog/post/rebac-rag)

Não incluí fonte oficial do GitHub Actions especificamente pra "pytest em
CI" — a busca só retornou tutoriais de terceiros, não a doc oficial do
GitHub. O padrão (`.github/workflows/*.yml` com trigger `pull_request`,
`actions/setup-python` + `pytest`) é conhecido e comum, mas não estou
citando um link oficial que não confirmei.

---

## 8. Aberto para discussão

Este documento não decide nada sozinho. Pontos que valem conversa antes de
qualquer implementação:

- Concordar com a ordem das Fases 0-5, ou reordenar por prioridade real do
  usuário (ex: se a apresentação na CETIC/UEMA tem prazo, MCP/tool-calling
  pode furar a fila mesmo sendo "Fase 5").
- Definir o escopo exato da Fase 0 (é um item isolado ou vira uma sessão
  própria de "fechar RBAC na main"?).
- Decidir se a Fase 2 (observabilidade) já entra com OpenTelemetry ou só
  com as métricas Prometheus puras — o custo de manter mais uma peça de
  infra pra 1 dev é real.
