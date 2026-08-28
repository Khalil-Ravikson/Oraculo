# 📍 Índice de Roadmap — Oráculo 2026–2027

> Ponto único de verdade. Navegação por tipo de leitor e fase.

---

## Para o Dono (Decisão em 2 min)

**Leia em ordem**:
1. 📄 [`ROADMAP_EXECUTIVO.md`](./ROADMAP_EXECUTIVO.md) — 1 página, visão + pergunta bloqueante.
2. 📄 [`decision_camada1_nodes.md`](./decision_camada1_nodes.md) — contexto da decisão + recomendação.

**Resultado esperado**: Decisão SIM/NÃO sobre Camada 1 (BaseNode + NodeRegistry).

---

## Para o Arquiteto (Visão Completa)

**Leia em ordem**:
1. 📄 [`estado_e_roteiro_planos.md`](./historico/estado_e_roteiro_planos.md) — O que foi feito (Fases 1–5) + o que falta.
2. 📄 [`fases_6_11_langgraph_studio.md`](./historico/fases_6_11_langgraph_studio.md) — Roadmap detalhado das próximas fases com inspiração LangGraph Studio.
3. 📄 [`CHECKLIST_PRE_FASE_6.md`](./CHECKLIST_PRE_FASE_6.md) — Validações pré-implementação.

**Resultado esperado**: Entendimento completo de sequência, dependências, riscos e impacto.

---

## Para o Dev (Começar a Codar)

**Passos**:
1. ✅ Validar checklist: [`CHECKLIST_PRE_FASE_6.md`](./CHECKLIST_PRE_FASE_6.md)
2. 📐 Ver especificação: seção "E. Validação e Testes" em [`fases_6_11_langgraph_studio.md`](./historico/fases_6_11_langgraph_studio.md)
3. 💻 Começar Sprint:
   - **Camada 1**: Criar `src/graph/base_node.py` + `node_registry.py` → herdar de BaseNode em LLM provider → testes verdes.
   - **Fase 6**: STT/TTS/Embeddings nodes → seguir padrão de Camada 1 → testes.

**Resultado esperado**: PR pronto pra code review.

---

## Estrutura Hierárquica de Documentos

```
docs/
├── ROADMAP_EXECUTIVO.md          ← COMEÇA AQUI (dono)
├── INDEX_ROADMAP_2026.md         ← você está aqui
├── decision_camada1_nodes.md      ← decisão + SIM/NÃO
├── CHECKLIST_PRE_FASE_6.md       ← validações
│
├── historico/
│   ├── estado_e_roteiro_planos.md               ← índice de Fases 1–5
│   ├── fases_6_11_langgraph_studio.md           ← roadmap Fases 6–11 (novo!)
│   ├── plataforma_orientada_a_configuracao.md   ← Fases 1–5, v2 (referência)
│   ├── pesquisa_arquitetura_producao.md         ← pesquisa de produção (histórico)
│   ├── arquitetura_nos_declarativa.md           ← proposta de Camada 1 (origem)
│   └── aposentadoria_dispatcher_legado.md       ← pré-req de Fase 2
│
├── architecture/
│   ├── system-map.md                           ← diagrama de componentes
│   ├── plano_frontend_ui_ux.md                  ← Plano B (implementado)
│   └── ... (outros)
│
├── business/
│   ├── regras_negocio_oraculo.md
│   └── ... (outros)
│
└── technical-debt.md                            ← dívidas registradas
```

---

## Fluxo de Leitura por Caso de Uso

### 📊 "Quero entender o estado do projeto inteiro"
1. `ROADMAP_EXECUTIVO.md`
2. `estado_e_roteiro_planos.md`
3. `fases_6_11_langgraph_studio.md` (seção A–B pra visão, seção C pra sequência)

**Tempo**: ~45 min. **Saída**: Entendimento 360º.

---

### 🎯 "Preciso decidir se começamos Camada 1 agora"
1. `ROADMAP_EXECUTIVO.md` (decisão bloqueante)
2. `decision_camada1_nodes.md` (benefícios/custos/risco)
3. `fases_6_11_langgraph_studio.md` seção B.1 (o que é Camada 1)

**Tempo**: ~20 min. **Saída**: SIM ou NÃO com justificativa.

---

### 🚀 "Vou começar Fase 6 segunda-feira, o que preciso saber?"
1. `CHECKLIST_PRE_FASE_6.md` (validar tudo que está verde)
2. `fases_6_11_langgraph_studio.md` seção "C. Roadmap" (Fase 6 exata)
3. `fases_6_11_langgraph_studio.md` seção "E. Validação e Testes" (definição de pronto)

**Tempo**: ~30 min. **Saída**: Pronto pra começar a codar.

---

### 🏗️ "Estou refatorando LLM provider pra herdar de BaseNode, é assim mesmo?"
1. `fases_6_11_langgraph_studio.md` seção "B.1 Camada de Nós Declarativa"
2. `fases_6_11_langgraph_studio.md` seção "E. Validação e Testes" (Camada 1)
3. `decision_camada1_nodes.md` seção "O que é Camada 1" (exemplo de código)

**Tempo**: ~15 min. **Saída**: Assinatura correta + testes esperados.

---

### 🎨 "Como fica o Hub com Graph Studio?"
1. `fases_6_11_langgraph_studio.md` seção "D. Hub — Redesign como Graph Studio"
2. `plano_frontend_ui_ux.md` seção "H. Onde entram as skills" (design system já pronto)

**Tempo**: ~20 min. **Saída**: Layout esperado + arquivos de UI.

---

### 🚨 "Há pré-requisitos que trancam Fase 8 (MCP)?"
1. `fases_6_11_langgraph_studio.md` seção "C. Roadmap" → Fase 8
2. `fases_6_11_langgraph_studio.md` seção "E. Validação e Testes" → Fase 8

**Tempo**: ~5 min. **Saída**: Tool calling nativo deve estar vivo em main; URL validation é obrigatória.

---

## Status Resumido (2026-08-28)

| Plano | Fase | Status | Documentação |
|---|---|---|---|
| **A (Config)** | 1–5 | ✅ Concluído, produção | `estado_e_roteiro_planos.md` |
| **A (Config)** | 6–8 | 🟡 Especificado, adiado | `fases_6_11_langgraph_studio.md` |
| **A (Config)** | 9–11 | 🟡 Especificado, condicional | `fases_6_11_langgraph_studio.md` |
| **B (Frontend)** | 0–5 | ✅ Concluído | `estado_e_roteiro_planos.md` |
| **Adendo (Nós)** | Camada 1 | 🟢 Pronto, bloqueante | `decision_camada1_nodes.md` |
| **Adendo (Nós)** | Camadas 2–3 | 🟡 Condicional | `fases_6_11_langgraph_studio.md` |

---

## Links Rápidos

| Pergunta | Vai pra... |
|---|---|
| "Quando começamos?" | `ROADMAP_EXECUTIVO.md` |
| "O que foi feito?" | `estado_e_roteiro_planos.md` |
| "Como fica a arquitetura?" | `fases_6_11_langgraph_studio.md` § A–B |
| "Qual é a sequência?" | `fases_6_11_langgraph_studio.md` § C |
| "O Hub fica como?" | `fases_6_11_langgraph_studio.md` § D |
| "Preciso de testes de quê?" | `fases_6_11_langgraph_studio.md` § E + `CHECKLIST_PRE_FASE_6.md` |
| "Camada 1 compensa?" | `decision_camada1_nodes.md` |
| "Qual é meu checklist?" | `CHECKLIST_PRE_FASE_6.md` |
| "Histórico — o que era antes?" | `plataforma_orientada_a_configuracao.md` (v2) ou `pesquisa_arquitetura_producao.md` |
| "Frontend vai mudar?" | `plano_frontend_ui_ux.md` (já concluído) |

---

## Questões Abertas (pra fechar com o dono)

1. **Camada 1 agora?** → Responder em `ROADMAP_EXECUTIVO.md`
2. **Quando é "virar produto" de verdade?** → Afeta Fases 9–11
3. **Qual é timeline realista?** → ~2 sprints/fase, sequencial
4. **Quem review? Qual critério de aprovação?** → Definir antes de Camada 1

---

## Histórico de Revisões

| Data | Mudança | Autor |
|---|---|---|
| 2026-08-28 | Criação de roadmap consolidado (Fases 6–11 + Camada 1) | @dono |
| 2026-08-27 | Plano B (frontend) concluído | @dev |
| 2026-08-26 | Plataforma v2 escrita (multi-tenancy, concorrência, secrets) | @arquiteto |
| 2026-08-25 | Fases 1–5 concluídas e movidas pra histórico | @dev |

---

**Última atualização**: 2026-08-28  
**Próxima revisão recomendada**: Após decisão de Camada 1 (3–5 dias)
