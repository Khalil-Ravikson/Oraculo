# Oráculo — Plano de Reorganização de Frontend (Scripts) e Redesign UI/UX

> **Status: IMPLEMENTADO (Fases 0–5), 2026-08-28.** As 14 rotas HTML do Hub
> estendem `_shell.html`; legado (`_base.html`, `hub.css`, `hub-bridge.css`,
> `hub_index.css`) removido. Pendência não-bloqueante: sign-off visual no
> browser. Ver `docs/historico/estado_e_roteiro_planos.md`.
>
> Produzido em 2026-08-27 a partir de auditoria completa de
> `static/` e `templates/` (16 templates, 7 CSS, 6 JS, ~8.700 linhas) mais
> pesquisa externa de referência (Linear, Stripe, Grafana, tipografia de
> dashboard 2026). Sem framework novo, sem build step novo — o objetivo é
> consertar uma base que hoje tem três sistemas de design competindo entre
> si na mesma aplicação, não substituir a stack (FastAPI + Jinja2 + CSS/JS
> vanilla) por outra coisa. React/build step aparece só no fim (§I) como
> extensão futura condicionada, não como parte deste plano.

---

## A. Diagnóstico — o estado real, com evidência

Este documento não parte de "vamos redesenhar", parte do que a auditoria
encontrou de fato ao ler cada arquivo:

### A.1 — Três sistemas de design competindo na mesma aplicação

| Sistema | Onde vive | Fontes carregadas | Paleta |
|---|---|---|---|
| "Terminal verde" | `static/css/hub.css`, carregado globalmente por `templates/hub/_base.html:13` | `Share Tech Mono` + `Rajdhani` (Google Fonts, carregadas) | Fundo quase-preto, texto verde neon (`--green: #00ff41`), scanline CRT animado (`body::before`, `hub.css:32-41`), grid de fundo animado |
| "Dark SaaS laranja" | `static/css/hub_index.css`, carregado **só** dentro de `hub/index.html:6` via `<link>` solto no meio do `content` block | `JetBrains Mono` — **nunca carregado via `<link>` em lugar nenhum do projeto** (confirmado por grep em todos os templates) | Fundo grafite, acento laranja `--accent: #ff6b35` |
| "Dark SaaS com Oxanium" | `templates/admin/test_area.html` (HTML solto, não estende `_base.html`) | `Oxanium` + `IBM Plex Mono` (Google Fonts, carregadas) | Paleta própria, sem relação com as outras duas |

**Consequência concreta, não hipotética**: `hub/index.html` carrega `hub.css`
(global, via `_base.html`) **e depois** `hub_index.css` por cima — os dois
definem `.card`/`.section-label`/`.status-pill` com nomes parecidos mas
propriedades diferentes, cascata resolvendo por ordem de carregamento, não
por intenção. `hub/login.html:23,39` referencia `var(--accent, #ff6b35)` —
uma variável que **não existe** em `hub.css` (que só define `--green`), o
fallback hardcoded é o único motivo da página não quebrar visualmente. Isso
não é estilo "cyberpunk vs SaaS", é uma variável de tema que aponta pro
sistema errado — bug real de CSS, não escolha estética.

### A.2 — "Tudo num código só": inline `<style>`/`<script>` em quase toda página

Contagem real (`grep -c "<style\|<script>"` por arquivo):

| Página | `<style>` inline | `<script>` inline | Linhas totais | CSS/JS externo próprio? |
|---|---|---|---|---|
| `config.html` | 1 (grande) | 1 (grande) | 569 | Não |
| `users.html` | 1 | 1 | 583 | Não |
| `eval.html` | 1 | 1 | 1078 | Não (existe `eval.css`/`eval.js` mas a página **também** tem bloco inline) |
| `audit.html` | 1 | 1 | 353 | Não |
| `llm_custo.html` | 1 | 1 | 396 | Não |
| `dashboard.html` | 1 | 1 | 457 | Não |
| `agents.html` | 1 | 1 | 293 | Não |
| `agent_prompt.html` | 1 | 1 | 173 | Não |
| `chunkviz.html` | 1 | 0 | 305 | Sim (`chunkViz.js`), mas ainda tem `<style>` embutido |
| `capabilities.html` | 1 | 1 | 99 | Não |
| `chat.html` | 0 | 1 | 144 | Não |
| `admin/login.html` | 1 | 0 | 72 | Não |

Onze das doze páginas do Hub têm CSS e/ou JS de página inteiros dentro do
HTML, em vez de em `static/css/`/`static/js/` — exatamente o padrão que foi
pedido para não repetir. Só `chunkviz.html`/`chat-debugger`/`monitor`/`eval`
têm arquivo próprio, e mesmo essas ainda carregam um `<style>` extra
embutido por cima.

### A.3 — Ícones: emoji em vez de sistema visual

Contagem de glifos emoji por página (`config.html` sozinho tem 33). Emoji
como ícone de produto tem três problemas práticos, não é só estética:
renderização inconsistente entre SO/navegador (a fonte de emoji do Windows
não é a do macOS não é a do Linux), não pode ser estilizado (cor, peso,
tamanho ficam presos ao que o SO desenhou), e não transmite "produto sério"
— nenhuma referência pesquisada (Linear, Stripe, Grafana, Vercel) usa emoji
como ícone de interface.

### A.4 — Detalhes que "quebram" silenciosamente

- **Nenhum favicon em todo o projeto** (`grep -rn "favicon" templates/
  static/` não retorna nada) — a aba do navegador mostra o ícone genérico
  do navegador em toda página do Hub/Admin/Monitor.
- **Numeração de card inconsistente** em `hub/index.html`: os cards são
  numerados `card-num` 01 a 11, mas "Portal Admin" e "Grafana" e
  "Prometheus" e "Jaeger" repetem os números 08/09/09/10/11 fora de ordem
  (`index.html:120-158` — "Custo" é 09, "Admin" volta a ser 08, "Grafana" é
  09 de novo). Como o próprio `frontend-design` (ver §B) observa, numeração
  só faz sentido quando a ordem carrega informação real — aqui não carrega
  nem está correta.
- **`JetBrains Mono`** é referenciado em `hub_index.css`/`config.html` como
  fonte primária mas **nunca é carregado** — todo usuário sem essa fonte
  instalada localmente (a maioria) vê o fallback (`Fira Code`/`Courier New`)
  sem ninguém ter decidido isso.
- **`templates/monitor/dashboard.html`** usa `style="..."` inline em
  atributos HTML (`padding: 20px; display: grid...`, linha 19) em vez de
  classe — outro sintoma do mesmo padrão.

Nada disso é readequação de gosto — é a lista de defeitos concretos que o
pedido "não quero páginas quebradas" está apontando.

---

## B. Direção de design — aplicando a skill `frontend-design`

Seguindo o processo da skill (brainstorm → plano de tokens → crítica →
construção): antes de escolher paleta/tipografia, fixar o brief.

**Sujeito**: console operacional interno — quem usa é o time técnico/admin
da UEMA/CTIC configurando providers de IA, revisando custo, auditando RAG,
gerenciando usuários. Não é uma landing page de produto, é uma cabine de
instrumentos. **Audiência**: técnica, usa a cada poucos dias, precisa de
densidade de informação e confiança visual, não de impacto de marketing.
**Job da página**: deixar o operador decidir rápido e com segurança — "essa
config está certa?", "esse provider está saudável?", "esse log é normal?".

**Por que abandonar os dois clichês, não só um**: a skill de design chama
atenção para três clusters que hoje saem "de graça" de qualquer geração de
IA — um deles é literalmente *"fundo quase-preto com um único acento verde
ácido ou vermelhão brilhante"*. O `hub.css` atual (`--bg: #050a05`,
`--green: #00ff41`, scanline CRT) é, sem querer, uma instância quase exata
desse clichê — não é "hacker cyberpunk como escolha", é o padrão-molde que
qualquer IA erra para o mesmo lugar. Trocar isso por um gradiente
azul-roxo genérico de SaaS seria só trocar um clichê por outro (o clichê
"AI slop" #2 da própria lista). A direção certa não é nem um nem outro —
é uma identidade pensada para o brief real.

### Tokens (compactos, para implementação seguir à risca)

**Cor** — paleta de instrumento, não neon nem gradiente. Base grafite fria
(não preto puro — preto puro achata contraste de sombra/profundidade),
UM acento — mantendo o laranja queimado que já existe no código (`#ff6b35`
em `hub_index.css`) porque já é uma escolha mais distinta que azul-SaaS
genérico, mas dessaturado e usado só como sinal de estado/ação, nunca como
decoração de fundo:

```
--ink-950   #0b0d10   fundo base (grafite frio, não preto puro)
--ink-900   #12151a   superfície de card/painel
--ink-800   #1b1f26   superfície elevada (hover, modal)
--line      #262b33   bordas/divisores
--paper     #e8eaed   texto primário (não branco puro — reduz vibração em telas)
--ink-500   #7b8394   texto secundário/legenda
--signal    #d97a3f   acento único — ação primária, foco, estado "ativo" (laranja queimado, dessaturado do #ff6b35 original)
--ok        #3ba55c   sucesso/saudável — só em badges de estado, nunca decorativo
--warn      #c9982e   atenção
--danger    #d9483f   erro/crítico
```

Regra dura herdada da pesquisa (Stripe/Grafana): **cor comunica estado, não
decora**. Se uma cor não está dizendo "isto está ok/atenção/erro/ativo",
ela não deveria estar lá.

**Tipografia** — par único para o produto inteiro (adeus às 3 famílias
concorrentes):

- Interface (rótulos, títulos, corpo): uma grotesca técnica — `Geist Sans`
  como primeira escolha (desenhada para densidade de dashboard,
  variable font, licença MIT/OFL, [Vercel — Geist](https://vercel.com/font)),
  com pilha de fallback `-apple-system, "Segoe UI", sans-serif`.
- Dados/monoespaçada (tabelas, custo em R$, IDs, logs, timestamps): `Geist
  Mono` — mesmo sistema tipográfico da interface, números tabulares
  (`font-variant-numeric: tabular-nums`) para colunas alinharem de verdade.
  Fallback `"IBM Plex Mono", ui-monospace, monospace`.

Só essas duas famílias, carregadas uma vez, num único `<link>` no `_base.html`
— elimina os 3 pares de fontes conflitantes hoje.

**Layout** — grade de 8px para todo espaçamento (`4/8/12/16/24/32/48/64`,
sem valores soltos tipo `18px`/`22px` que aparecem hoje espalhados),
`border-radius` único de 8px para cards/inputs e 6px para botões/badges
(nada de esquinas retas tipo `hub.css` nem excesso de arredondamento tipo
pill em tudo), sidebar fina fixa (~72px colapsada / 220px expandida,
padrão Linear) substituindo o header horizontal duplicado entre `_base.html`
e cada página, conteúdo em grade de 12 colunas com `max-width: 1280px`.

**Assinatura** — um elemento memorável, não decorativo: o Oráculo já tem
algo genuinamente sequencial e real — o Supervisor resolve intenção em 5
camadas, da mais barata pra mais cara (`docs/business/regras_negocio_oraculo.md`
item 8). Em vez de números decorativos nos cards de navegação (que hoje
nem estão corretos, ver §A.4), o elemento de assinatura vira um **indicador
de camada** — uma pequena régua horizontal de 5 segmentos, usada no
dashboard e no card de "status do sistema", mostrando em qual camada cada
decisão de roteamento foi resolvida agora mesmo (regex / heurística /
regex-config / semântica / LLM). É informação real do sistema, não
enfeite — e é único do Oráculo, nenhum dashboard genérico teria isso.

**Ícones**: substituir todo emoji por um set SVG consistente, stroke de
1.5px, 20px de grade — [Lucide](https://lucide.dev/) (MIT, ~1.500 ícones,
sem dependência de build, pode ser inline SVG servido de `static/icons/`)
ou [Phosphor Icons](https://phosphoricons.com/) (MIT) como alternativa.
Recomendação: Lucide, por ter o conjunto mais consistente pra ícones de
"sistema/dados" (database, cpu, activity, shield) que o Hub precisa.

**Aba do navegador**: `<title>` já segue majoritariamente o padrão
`{Página} — Oráculo UEMA` — só padronizar os que fogem (`chat.html`,
conferir todos) — e adicionar favicon real: um SVG simples derivado do
glifo de assinatura (a régua de 5 segmentos, ou um "O" geométrico com o
acento `--signal`), não o emoji de cristal atual.

---

## C. Arquitetura de arquivos — CSS

Objetivo: **nenhuma página nova precisa de `<style>` inline** para casos
comuns; só overrides específicos e pequenos, se sobrar algum.

```
static/css/
├── tokens.css          # :root — cores, fontes, espaçamento, radius, sombra (§B)
├── base.css            # reset + html/body + tipografia base + foco visível
├── layout.css          # shell: sidebar, topbar, main, grid de conteúdo
├── components/
│   ├── card.css        # .card (nav-card, stat-card, panel-card — 1 API só)
│   ├── table.css       # tabela densa (Stripe-like): tabular-nums, hover de linha, sticky header
│   ├── badge.css        # estado (ok/warn/danger/neutro) — cor só aqui
│   ├── button.css        # primário/secundário/ghost/destrutivo, estados hover/focus/disabled
│   ├── form.css        # input/select/textarea/toggle, mensagens de erro
│   ├── modal.css        # diálogo/confirmação (hoje cada página reimplementa a própria)
│   └── toast.css        # notificação de sucesso/erro (hoje cada página tem a própria)
├── pages/
│   ├── login.css
│   ├── dashboard.css
│   ├── config.css
│   ├── agents.css
│   ├── users.css
│   ├── audit.css
│   ├── llm-custo.css
│   ├── chat.css
│   ├── chunkviz.css     # já existe — só remove o <style> inline remanescente
│   ├── eval.css          # já existe — idem
│   └── monitor.css        # já existe, revisar contra tokens novos
└── icons/                 # SVGs individuais (Lucide subset usado), servidos estáticos
```

Cada página carrega `tokens.css` + `base.css` + `layout.css` +
`components/*.css` (via `_base.html`, uma vez, sempre) e **só** o próprio
`pages/<nome>.css` — nunca um `<link>` solto no meio do `content` block
como `index.html:6` faz hoje.

## D. Arquitetura de arquivos — JS

Mesmo princípio, e resolve um problema extra: hoje cada página reimplementa
seu próprio `fetch()` com header JWT, seu próprio toast, seu próprio modal
de confirmação — 8+ implementações levemente diferentes do mesmo código.

```
static/js/
├── core/
│   ├── api-client.js    # fetch() único: injeta JWT, trata 401 (redirect login), trata erro padrão
│   ├── toast.js          # showToast(msg, tipo) — usado por toda página
│   ├── modal.js          # confirmação genérica (substitui confirm() nativo do browser)
│   └── format.js          # formatação de moeda BRL, data, número — hoje duplicado em vários JS
├── components/
│   ├── status-dot.js      # atualização de indicador de saúde (usado no header e nos cards)
│   └── layer-indicator.js  # o elemento de assinatura (§B) — 1 componente, reusado
└── pages/
    ├── dashboard.js
    ├── config.js
    ├── agents.js
    ├── users.js
    ├── audit.js
    ├── llm-custo.js
    ├── chat.js (hoje chat-debugger.js — renomear por consistência)
    ├── chunkviz.js         # já existe, ajustar pra usar core/api-client
    ├── eval.js              # já existe, idem
    └── monitor.js            # já existe, idem
```

`core/` e `components/` carregados uma vez no `_base.html` (ou só nas
páginas que precisam — `api-client`/`toast` praticamente todas). Cada
`pages/<nome>.js` só contém lógica específica daquela tela — o que hoje
está embutido em `<script>` sai para cá.

## E. Templates Jinja2 — o que muda em `_base.html`

`_base.html` (hoje 24 linhas, minimalista até demais — não define shell
nenhum) passa a fornecer a casca comum de verdade: sidebar de navegação
(as 11 entradas hoje hardcoded em `index.html` viram a navegação global,
não um "menu inicial" que some ao entrar em qualquer página — hoje, entrar
em `/hub/config` perde toda navegação, o usuário só tem o botão "voltar"),
topbar com status/logout, `<link>` dos CSS base, blocos `extra_css`/
`extra_js`/`content` como já existe. Isso resolve um problema de UX real
não listado ainda: **hoje só a página inicial do Hub tem navegação — as
outras 10 páginas são becos sem saída**, cada uma com seu próprio
botão "voltar" estilizado diferente.

---

## F. Sistema de componentes — regras concretas (sem ambiguidade de padding)

- **Espaçamento**: todo `padding`/`margin`/`gap` vem da escala de 8px do
  token. Nenhum valor mágico tipo `14px`/`18px`/`22px` (todos presentes
  hoje espalhados pelo código) — isso sozinho elimina a maior causa de
  "olho reconhece que algo está errado mas não sabe dizer o quê".
- **Cards**: uma única classe base `.card` com variantes por modificador
  (`.card--nav`, `.card--stat`, `.card--panel`), nunca 3 nomes de classe
  competindo (`.card`/`.nav-card`/`.stat-card` como hoje) para o mesmo
  conceito visual.
- **Tabelas** (usadas em `users.html`, `audit.html`, `llm_custo.html`,
  `eval.html`): linha com altura fixa, números com `tabular-nums`, cabeçalho
  fixo (`sticky`) em listas longas, estado vazio desenhado (nunca só texto
  "nenhum resultado" solto).
- **Botões**: 4 variantes só (primário/secundário/ghost/destrutivo), estado
  de foco visível obrigatório (`:focus-visible`, hoje ausente na maioria),
  nunca `cursor: pointer` sem `transition` (efeito de clique "morto" que
  várias páginas têm hoje).
- **Acessibilidade mínima, sem virar projeto à parte**: contraste
  AA (4.5:1) em todo texto sobre fundo — o texto `--text-dim`/`--muted`
  atual em `hub.css`/`hub_index.css` fica abaixo disso sobre o fundo escuro
  em alguns casos, conferir com os tokens novos; `prefers-reduced-motion`
  respeitado (hoje `hub.css` tem 5+ `@keyframes` sempre ativas — scanline,
  blink, pulse — sem checagem nenhuma).

---

## G. Migração por fases (risco crescente, sem quebrar nada em produção)

> **Todas concluídas em 2026-08-28.** Notas de execução ao lado de cada fase.

| Fase | Escopo | Estado |
|---|---|---|
| **0** | `tokens.css`, `base.css`, `layout.css`, `components/*.css`, `core/*.js` — construídos e testados isoladamente em `/hub/_styleguide` | ✅ |
| **1** | shell real (`_shell.html`, sidebar+topbar); `index.html`, `login.html` migradas (`dashboard.html` era órfã → removida na Fase 4) | ✅ |
| **2** | `config.html`, `agents.html`, `users.html` | ✅ |
| **3** | `audit.html`, `llm_custo.html`, `chat.html`, `chunkviz.html`, `eval.html`, `capabilities.html`, `agent_prompt.html`, `routes.html` | ✅ (Chart.js vendorado local; chat com design novo de bolhas; chunkviz com JS re-modularizado) |
| **4** | remoção do legado: `_base.html`, `hub-bridge.css`, `hub.css`, `hub_index.css`, `admin/*`, `monitor/*` (+ `src/api/monitor.py`), `hub/dashboard.html`, JS órfãos | ✅ (todos eram código morto sem rota — decisão registrada nas AskUserQuestion do dono) |
| **5** | Auditoria final: `<title>` (auto via `_shell`), favicon (data-URI SVG no `_shell`), contraste, tab order + foco visível (fix global no `form.css`), 375px, zero emoji, zero `<style>`/`<script>` inline, zero `on*=` | ✅ |

Critério de "pronto" por página (checklist literal, não opinião):

- [ ] Sem `<style>` nem `<script>` inline — só `<link>`/`<script src>`.
- [ ] Sem emoji — ícone SVG do set escolhido em todo lugar que hoje tem emoji.
- [ ] Usa só `tokens.css` (nenhuma cor/fonte hardcoded na página).
- [ ] Título de aba no padrão `{Página} — Oráculo UEMA`; favicon carregando.
- [ ] Navegação (sidebar) presente e funcional a partir desta página.
- [ ] Testado em 375px de largura sem overflow horizontal.
- [ ] `Tab` percorre todos os controles em ordem lógica, com foco visível.
- [ ] Nenhum erro no console do navegador ao carregar e ao usar a página.

---

## H. Onde entram as skills do projeto

- **`frontend-design`** (`.claude/skills/frontend-design/`): usar
  explicitamente no início da Fase 0 — antes de escrever qualquer CSS de
  token, rodar o processo da skill (brainstorm de paleta/tipo/layout/
  assinatura → crítica contra o brief §B → só então construir), e de novo
  em qualquer página cujo layout de conteúdo for genuinamente novo (não
  reaproveitamento de componente já existente).
- **`feature-dev`** (`.claude/skills/feature-dev/`, comando `/feature-dev`):
  usar para conduzir cada Fase da §G como uma feature própria — Discovery
  (confirmar escopo exato da fase com quem aprovou este plano), Exploração
  (o `code-explorer` já teria a maior parte do trabalho feito por este
  documento, mas vale rodar por página nova não coberta aqui),
  Arquitetura (para decisões não cobertas neste plano, ex.: como a sidebar
  deve se comportar em telas médias), Implementação, e principalmente
  **Revisão de qualidade** (fase 6 do plugin) — os 3 agentes
  `code-reviewer` em paralelo (simplicidade/bugs/convenções) são o
  mecanismo certo pra pegar exatamente o tipo de coisa que gerou este
  diagnóstico (CSS duplicado, variável de tema inexistente, inline
  style/script) antes de considerar uma fase concluída.
- Nenhuma outra skill do projeto se aplica diretamente a UI — `run` pode
  ser usado ao final de cada fase pra efetivamente abrir o Hub num
  navegador e conferir visualmente antes de marcar a fase como pronta
  (o próprio CLAUDE.md do projeto pede teste visual real para mudança de
  frontend, não só ausência de erro).

---

## I. Extensão futura (não implementada agora): componente framework leve

Fora do escopo deste plano, registrado como possibilidade condicional —
**não avaliar até que a Fase 2 (as páginas mais complexas, `config.html`/
`users.html`/`agents.html`) mostre de fato dificuldade real em manter
estado de UI só com vanilla JS** (múltiplos formulários dependentes,
listas com filtro+paginação+edição inline simultâneos).

Se/quando isso acontecer, a escala de opção, da menor mudança pra maior:

1. **Alpine.js** — sem build step, se declara via atributo HTML
   (`x-data`, `x-show`), adequado pra interatividade de página isolada sem
   reescrever o modelo de templates Jinja2 atual. Menor risco de todos.
2. **htm + Preact** — sintaxe parecida com JSX sem precisar de bundler
   (`htm` compila em runtime), componente real com estado, ainda servido
   como `<script>` estático.
3. **React com Vite** — só se o Hub crescer a ponto de justificar um SPA de
   verdade com build step, roteamento client-side e um time dedicado a
   frontend — não é o cenário de hoje (~1 dev, 12 páginas server-rendered).

Decisão explícita: **nenhuma dessas três é adotada neste plano.** Fica
registrada para não precisar redescobrir as opções do zero se a pergunta
"isso já não dá mais pra fazer com JS puro" surgir depois da Fase 2.

---

## J. Fontes externas usadas nesta pesquisa

- [Lollypop — B2B SaaS Typography Rules for a Dashboard UI](https://lollypop.design/blog/2026/july/enterprise-saas-typography-rules/)
- [DiverseKit — Geist vs Inter vs Satoshi: Which UI Font Wins?](https://diversekit.com/blog/geist-vs-inter)
- [Vercel — Geist font](https://vercel.com/font)
- [Hermes Agent — Popular Web Designs: 54 real design systems (Stripe, Linear, Vercel)](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/creative/creative-popular-web-designs)
- [AdminLTE — Admin Dashboard Design: Principles, Layouts & Examples (2026)](https://adminlte.io/blog/admin-dashboard-design/)
- [Lucide Icons](https://lucide.dev/)
- [Phosphor Icons](https://phosphoricons.com/)

Guia interno seguido à risca: `.claude/skills/frontend-design/skills/frontend-design/SKILL.md`
(processo de brainstorm/tokens/crítica, e a lista explícita dos 3 clichês
de design gerado por IA a evitar).
