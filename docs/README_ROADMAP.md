# 🗺️ Oráculo Roadmap 2026–2027 — Comece aqui

**Status**: Fases 1–5 ✅ prontas. Fases 6–11 — **sua decisão esta semana**.

---

## ⚡ 30 segundos

**Pergunta**: Quer um **Graph Studio visual no Hub** (tipo LangGraph Studio, mas rodando em produção)?

- **SIM**: Camada 1 (BaseNode + Registry) agora → 1–2 sprints → depois Fases 6–8
- **NÃO**: Fases 6–8 ficam adiadas (sem alicerce)

**Próximo passo**: Leia [`ROADMAP_EXECUTIVO.md`](./ROADMAP_EXECUTIVO.md) (1 página, 2 min).

---

## 📚 Navegação (qual documento ler?)

| Você é... | Leia... | Tempo |
|---|---|---|
| **Dono/decisor** | [`ROADMAP_EXECUTIVO.md`](./ROADMAP_EXECUTIVO.md) | 2 min |
| **Arquiteto** | [`INDEX_ROADMAP_2026.md`](./INDEX_ROADMAP_2026.md) → [`fases_6_11_langgraph_studio.md`](./historico/fases_6_11_langgraph_studio.md) | 60 min |
| **Dev** | [`CHECKLIST_PRE_FASE_6.md`](./CHECKLIST_PRE_FASE_6.md) → começar a codar | 30 min |
| **Curioso** | [`estado_e_roteiro_planos.md`](./historico/estado_e_roteiro_planos.md) | 20 min |

---

## 🎯 Roadmap visual

```
2026-08-28 (hoje)
  ✅ Fases 1–5 completas
  ✅ Frontend redesenhado
  ⏳ Camada 1? (SIM/NÃO)
         ↓
    [CAMADA 1]
    BaseNode + Registry
    (1–2 sprint)
         ↓
    [FASE 6]      [FASE 7]      [FASE 8]
    STT/TTS      Canais        MCP
    (demanda)    (demanda)     (demanda+pré-req)
         ↓            ↓            ↓
    ────────────────────────────────────
         ↓
    [FASE 9]      [FASE 10]      [FASE 11]
    Multi-tenant  Secrets Manager  GitOps
    (2º cliente)  (compliance)    (cliente pede)
```

---

## 📄 Documentos-chave (2026-08-28)

1. **Nova proposta**: [`fases_6_11_langgraph_studio.md`](./historico/fases_6_11_langgraph_studio.md)
   - Roadmap completo Fases 6–11
   - Inspiração LangGraph Studio
   - Sequência, risco, teste

2. **Decisão bloqueante**: [`decision_camada1_nodes.md`](./decision_camada1_nodes.md)
   - Camada 1 vale a pena?
   - Recomendação: SIM

3. **Estado atual**: [`estado_e_roteiro_planos.md`](./historico/estado_e_roteiro_planos.md)
   - O que foi feito (Plano A + B)
   - O que falta

4. **Pré-requisitos**: [`CHECKLIST_PRE_FASE_6.md`](./CHECKLIST_PRE_FASE_6.md)
   - 50+ checklist items
   - Valide antes de começar

5. **Índice completo**: [`INDEX_ROADMAP_2026.md`](./INDEX_ROADMAP_2026.md)
   - Navegação por caso de uso
   - Links rápidos

---

## ✅ Próximas 48 horas

1. **Hoje**: Leia `ROADMAP_EXECUTIVO.md` (2 min)
2. **Hoje**: Decida: Camada 1 agora? (SIM/NÃO)
3. **Amanhã**: Se SIM → passe `decision_camada1_nodes.md` pra time técnico
4. **Amanhã**: Se SIM → schedule Sprint X pra refactor LLM + BaseNode

---

## 🤔 FAQ rápido

**P: Camada 1 é imprescindível?**  
R: Não, mas reduz risco das Fases 6–8 em 50%. Recomendado.

**P: Quanto tempo cada fase?**  
R: 1–3 sprints (Fases 6–8 sequenciais; 9–11 condicionais).

**P: Fase 6 sai quando?**  
R: Após Camada 1 + SIM estar aprovado (2–3 semanas).

**P: O Hub vai quebrar?**  
R: Não. É extensão pura (novas seções, sem tocar o que existe).

---

## 🔗 Atalhos

- **Começar design do Graph Studio**: [`fases_6_11_langgraph_studio.md`](./historico/fases_6_11_langgraph_studio.md) § D
- **Ver código esperado de Camada 1**: [`decision_camada1_nodes.md`](./decision_camada1_nodes.md) § "O que é Camada 1"
- **Validar pré-requisitos**: [`CHECKLIST_PRE_FASE_6.md`](./CHECKLIST_PRE_FASE_6.md)
- **Histórico antigo**: [`plataforma_orientada_a_configuracao.md`](./historico/plataforma_orientada_a_configuracao.md)

---

**TL;DR**: Leia `ROADMAP_EXECUTIVO.md`, decida, e avance. 🚀
