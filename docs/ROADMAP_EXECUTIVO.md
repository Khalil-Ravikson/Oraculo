# Oráculo — Roadmap 2026–2027 (1 página)

**Status actual (2026-08-28)**: Fases 1–5 ✅ concluídas, em produção. Plano de frontend ✅ concluído. Fases 6–11 precisam de decisão.

---

## Visão: Graph Studio Visual (tipo LangGraph Studio, mas em produção)

Hoje: Hub admin edita toggles de providers.  
Amanhã: Hub é um **editor visual de grafos** — arrastar nós (STT, LLM, TTS, tools), conectar, visualizar fluxo ao vivo.

---

## Pergunta bloqueante (SIM ou NÃO?)

**Iniciar Camada 1 (BaseNode + NodeRegistry) agora?**

| Aspecto | Resposta |
|---|---|
| Desbloqueia Fases 6–8? | ✅ SIM |
| Quanto tempo? | 1–2 sprints |
| Risco? | Baixo-médio (refatoração de código vivo, mas com testes) |
| Recomendação? | **🟢 SIM — começa junto com Fase 6** |

**Se NÃO**: Fases 6–8 ficam sem alicerce; risco sobe.

---

## Sequência de Fases (se SIM)

| Fase | Escopo | Gatilho | Status |
|---|---|---|---|
| **Camada 1** | BaseNode + Registry — unifica LLM, STT, TTS, Embeddings | Decisão acima | 🟢 Pronto |
| **Fase 6** | STT/TTS/Embeddings como Nodes | Demanda ou terrain-prep | 🟢 Especificado |
| **Fase 7** | Canais (Telegram, Slack) como ChannelNodes | Demanda concreta | 🟡 Adiado, alto risco |
| **Fase 8** | MCP como ToolProviderNode | Demanda + pré-req (tool-calling nativo em main) | 🟡 Adiado, alto risco |
| **Fase 9** | Multi-tenancy real | 2º cliente real OU decisão de negócio | 🟡 Condicional |
| **Fase 10** | Secrets Manager (BYOK) | Cliente enterprise ou venda a 3º | 🟡 Condicional |
| **Fase 11** | GitOps (YAML + PR) | Cliente pedir versionamento via Git | 🟡 Condicional |

**Fases 2–Adendo (Camadas 2–3 de nós)**: Não iniciar até Camada 1 provar valor em produção (Fase 6).

---

## Hub vai virar "Graph Studio"

Novas seções:
- `/hub/graph-studio` — editor visual (canvas com nós/arestas, Lucide icons, Konva.js)
- `/hub/graph-nodes` — registry de todos os nós (STT, LLM, Tools, etc.)
- `/hub/channels` — gerenciar canais (se Fase 7)
- `/hub/mcp-servers` — MCP registry (se Fase 8)

Inspiração: LangGraph Studio, mas rodando ao vivo em produção.

---

## Próximos passos imediatos (1–2 dias)

1. **Decidir**: Camada 1 agora? → SIM ✅ ou NÃO ❌
2. **Se SIM**:
   - Sprint X: Refatorar LLM + criar BaseNode + NodeRegistry.
   - Depois: Fase 6 (STT/TTS/Embeddings nodes).
3. **Sign-off visual** do Plano B (abrir `localhost:9000/hub/`, revisar 14 páginas).
4. **Validar pré-req de Fase 8**: Tool-calling nativo (`google.genai` com `bind_tools()`) está em main?

---

## Documentos de referência

- **`fases_6_11_langgraph_studio.md`** — Detalhamento completo (implementação, testes, risco).
- **`decision_camada1_nodes.md`** — Contexto + benefícios/custos da decisão.
- **`estado_e_roteiro_planos.md`** — Índice único de verdade do projeto.

---

**Sua decisão?** → Edite esta linha: Camada 1 **[ SIM / NÃO ]**
