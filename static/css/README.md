# Frontend — arquitetura de arquivos (Plano B)

> `docs/architecture/plano_frontend_ui_ux.md` §C/§D/§E. Migração por fases (§G).

## CSS (`static/css/`)

```
tokens.css          :root — cor / tipo / espaço / raio / sombra / shell. Fonte única.
base.css            reset + tipografia base + foco visível + scrollbar
layout.css          casca: sidebar fina fixa, topbar, main, grade de 12 colunas
components/
  button.css        4 variantes (primary / secundário / ghost / danger)
  card.css          .card + modificadores (--nav / --stat / --panel) — 1 API só
  badge.css         estado (ok/warn/danger/active/neutral) — cor vive AQUI
  table.css         tabela densa: tabular-nums, header sticky, hover, estado vazio
  form.css          input/select/textarea/toggle + mensagem de erro
  modal.css         diálogo/confirmação (dirigido por core/modal.js)
  toast.css         notificação (dirigido por core/toast.js)
  layer-indicator.css  ASSINATURA — régua de 5 segmentos das camadas do Supervisor
```

```
pages/
  routes.css        escopado sob .routes-view
  chunkviz.css      escopado sob .cv-
  eval.css          escopado sob .eval-dash
  chat.css          escopado sob .cx-
```

Cada página carrega `tokens + base + layout + components/*` uma vez (via
`_shell.html`) e **só** o próprio `pages/<nome>.css` no `{% block extra_css %}`.
Nunca `<link>` solto no meio do `content`. Regras de página ficam escopadas
sob um ancestral único da página (nunca classe genérica no topo).

## JS (`static/js/`, ES modules)

```
core/
  api-client.js     fetch() único: cookie JWT, trata 401, normaliza erro (ApiError.isConflict)
  toast.js          showToast(msg, tipo)
  modal.js          confirmar({titulo, corpo, acao, perigo}) -> Promise<boolean>
  format.js         fmt.brl / .num / .usd / .dateTime / .duracao / .esc
components/
  layer-indicator.js  renderLayerRule(el, {camada, rota})
```

## Direção (§B)

Painel de instrumento. Base grafite fria (`--ink-950 #0b0d10`, não preto puro),
UM acento — laranja queimado **dessaturado** (`--signal #d97a3f`, do `#ff6b35`
que já existia no código) usado só como sinal de estado/ação, nunca fundo.
Regra dura: **cor comunica estado** (ok/atenção/erro/ativo) — se não diz isso,
não deveria estar lá.

Tipografia: **Geist** + **Geist Mono** (par único, Google Fonts, um `<link>`).
Números tabulares nas colunas de dado.

Assinatura: o **indicador de camada** — o Supervisor resolve intenção em 5
camadas (regex → heurística → regex-config → semântica → LLM); a régua mostra
onde a última decisão parou. Dado real do sistema, não enfeite; único do Oráculo.

Ícones: SVG inline stroke 1.5 (Lucide), nunca emoji.

## Progresso da migração (§G)

- **Fase 0** — tokens/base/layout/components + core JS, referência em `/hub/_styleguide`. ✅
- **Fases 1–3** — todas as 14 páginas do Hub estendem `_shell.html` direto. ✅
- **Fase 4** — `_base.html`, `hub-bridge.css`, `hub.css`, `hub_index.css` e os
  templates órfãos (`admin/*`, `monitor/*`, `hub/dashboard.html`) removidos. ✅
- **Fase 5** — auditoria final (contraste AA, teclado, 375px, favicon/title). Pendente.
