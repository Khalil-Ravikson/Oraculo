/**
 * eval.js — Dashboard RAG Live | Oráculo UEMA
 * Responsabilidades:
 *   - Tabs (left / right)
 *   - SSE log terminal
 *   - Pipeline query SSE
 *   - Calendário (fetch + render)
 *   - Métricas e histórico de sessão
 */

// ── Estado global ─────────────────────────────────────────────────────────
const S = {
  logEs:       null,
  queries:     0,
  totalTokens: 0,
  stepCards:   {},
};

// ── Utils ─────────────────────────────────────────────────────────────────
const esc = s =>
  String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

const fmt = n =>
  n >= 1e6 ? (n/1e6).toFixed(1)+'M' :
  n >= 1e3 ? (n/1e3).toFixed(1)+'k' :
  String(n);

const $ = id => document.getElementById(id);
const set = (id, val) => { const el = $(id); if (el) el.textContent = val; };

// ── Tabs ──────────────────────────────────────────────────────────────────
function setupTabs(containerSel, tabIds, panelPrefix) {
  const container = document.querySelector(containerSel);
  if (!container) return;

  container.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      const key = tab.dataset.tab;
      tabIds.forEach(k => {
        const el = $(panelPrefix + k);
        if (!el) return;
        // terminal precisa de display:flex para ocupar 100% da altura
        el.style.display = k === key
          ? (k === 'terminal' ? 'flex' : 'block')
          : 'none';
      });
    });
  });
}

// ── Status header ─────────────────────────────────────────────────────────
function setStatus(ok) {
  const dot = $('dot-live');
  if (dot) dot.className = 'dot ' + (ok ? 'live' : '');
  set('status-txt', ok ? 'stream ativo' : 'desconectado');
}

// ── Log Terminal ──────────────────────────────────────────────────────────
function connectLogs() {
  if (S.logEs) S.logEs.close();

  // O eval/stream emite eventos de progresso de avaliação.
  // Para logs reais do servidor, é preciso um endpoint dedicado.
  // Por enquanto exibimos mensagens do eval/stream como log.
  const es = new EventSource('/eval/stream');
  S.logEs = es;

  es.onopen  = () => setStatus(true);
  es.onerror = () => {
    setStatus(false);
    setTimeout(connectLogs, 4000);
  };

  es.onmessage = e => {
    try {
      const d = JSON.parse(e.data);
      if (!d || d.type === 'ping') return;
      appendLog({
        ts:    new Date().toLocaleTimeString('pt-BR'),
        level: d.type?.toUpperCase() ?? 'INFO',
        name:  'eval',
        msg:   JSON.stringify(d),
        cor:   d.type === 'done' ? 'var(--accent)' :
               d.type === 'error' ? 'var(--red)' : 'var(--muted)',
      });
    } catch { /* JSON malformado */ }
  };
}

function appendLog(d) {
  const term = $('terminal');
  if (!term) return;
  const atBottom = term.scrollTop + term.clientHeight >= term.scrollHeight - 20;

  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML =
    `<span class="log-ts">${esc(d.ts)}</span>` +
    `<span class="log-level" style="color:${d.cor??'var(--text)'}">${esc(d.level)}</span>` +
    `<span class="log-name">${esc(d.name)}</span>` +
    `<span class="log-msg">${esc(d.msg)}</span>`;

  term.appendChild(line);
  while (term.children.length > 500) term.removeChild(term.firstChild);
  if (atBottom) term.scrollTop = term.scrollHeight;
}

// ── Pipeline Steps ────────────────────────────────────────────────────────
const STEPS_DEF = [
  { id: 'routing',   label: '1 — Routing Semântico (Redis KNN)' },
  { id: 'transform', label: '2 — Query Transform' },
  { id: 'retrieval', label: '3 — Busca Híbrida (BM25 + Vetor)' },
  { id: 'geracao',   label: '4 — Geração (Gemini Flash)' },
];

const ICONS = { pending:'○', running:'◌', ok:'✓', warn:'⚠', error:'✗', skip:'—' };

function buildSteps() {
  const area = $('steps-area');
  area.innerHTML = '';
  S.stepCards = {};

  STEPS_DEF.forEach(s => {
    const card = document.createElement('div');
    card.className = 'step-card';
    card.id = 'step-' + s.id;
    card.innerHTML =
      `<div class="step-header">` +
        `<div class="step-icon pending" id="si-${s.id}">${ICONS.pending}</div>` +
        `<div class="step-label">${esc(s.label)}</div>` +
        `<div class="step-ms" id="sms-${s.id}"></div>` +
      `</div>` +
      `<div class="step-result" id="sr-${s.id}"></div>`;
    area.appendChild(card);
    S.stepCards[s.id] = card;
  });

  const ra = $('resposta-area');
  if (ra) ra.classList.remove('visible');

  const ca = $('chunks-area');
  if (ca) ca.innerHTML = '';
}

function stepRunning(id) {
  const card = $('step-' + id);
  if (!card) return;
  card.className = 'step-card running';
  const icon = $('si-' + id);
  if (icon) { icon.className = 'step-icon running'; icon.textContent = ICONS.running; }
}

function stepDone(id, result, badge, ms) {
  const card = $('step-' + id);
  if (!card) return;

  const cardCls =
    badge === 'ok' || badge === 'transformed' ? 'done-ok' :
    badge === 'skip' || badge === 'warn'       ? 'done-warn' :
    badge === 'error' || badge === 'blocked'   ? 'done-error' : 'done-ok';

  card.className = 'step-card ' + cardCls;

  const iconCls =
    badge === 'ok' || badge === 'transformed' ? 'ok' :
    badge === 'skip' || badge === 'warn'       ? 'warn' :
    badge === 'error' || badge === 'blocked'   ? 'error' : 'ok';

  const icon = $('si-' + id);
  if (icon) {
    icon.className = 'step-icon ' + iconCls;
    icon.textContent = ICONS[iconCls] ?? '✓';
  }

  const msEl = $('sms-' + id);
  if (msEl && ms != null) msEl.textContent = ms + 'ms';

  const resEl = $('sr-' + id);
  if (resEl && result) {
    resEl.textContent = result;
    resEl.classList.add('visible');
  }
}

function addChunk(d) {
  const area = $('chunks-area');
  if (!area) return;
  if (area.querySelector('.empty-state')) area.innerHTML = '';

  const scoreColor =
    d.score >= 0.03 ? 'var(--accent)' :
    d.score >= 0.015 ? 'var(--amber)' : 'var(--red)';

  const card = document.createElement('div');
  card.className = 'chunk-card';
  card.innerHTML =
    `<div class="chunk-header">` +
      `<span class="chunk-source">${esc(d.source)}</span>` +
      `<span class="chunk-score" style="color:${scoreColor}">RRF: ${d.score}</span>` +
    `</div>` +
    `<div class="chunk-preview">${esc(d.preview)}</div>`;
  area.appendChild(card);
}

function showResposta(texto, fonte, tokens) {
  const area = $('resposta-area');
  if (!area) return;
  area.classList.add('visible');
  set('resposta-texto', texto);
  set('resposta-fonte', `fonte: ${fonte}${tokens ? ' | ' + tokens + ' tokens gerados' : ''}`);
}

// ── Métricas ──────────────────────────────────────────────────────────────
function updateMetrics(d) {
  set('m-tokens-total', fmt(d.tokens_total ?? 0));
  set('m-tokens-in',    fmt(d.tokens_entrada ?? 0));
  set('m-tokens-out',   fmt(d.tokens_saida ?? 0));
  set('m-latencia',     (d.latencia_ms ?? 0) + 'ms');
  set('m-rota',         d.rota ?? '—');

  const score  = d.crag_score ?? 0;
  const scoreEl = $('m-crag');
  if (scoreEl) {
    scoreEl.textContent = score.toFixed(3);
    scoreEl.style.color =
      score >= 0.4 ? 'var(--accent)' :
      score >= 0.2 ? 'var(--amber)' : 'var(--red)';
  }

  const fill = $('crag-fill');
  if (fill) {
    fill.style.width      = Math.min(score / 0.6 * 100, 100) + '%';
    fill.style.background =
      score >= 0.4 ? 'var(--accent)' :
      score >= 0.2 ? 'var(--amber)' : 'var(--red)';
  }

  S.queries++;
  S.totalTokens += (d.tokens_total ?? 0);
  set('s-queries', S.queries);
  set('s-tokens',  fmt(S.totalTokens));
}

// ── Histórico ─────────────────────────────────────────────────────────────
function addHistorico(d, pergunta) {
  const list = $('hist-list');
  if (!list) return;
  if (list.querySelector('.empty-state')) list.innerHTML = '';

  const score = d.crag_score ?? 0;
  const scoreColor =
    score >= 0.4 ? 'var(--accent)' :
    score >= 0.2 ? 'var(--amber)' : 'var(--red)';

  const item = document.createElement('div');
  item.className = 'hist-item';
  item.innerHTML =
    `<span class="col-w80 col-accent">${esc(d.rota ?? '—')}</span>` +
    `<span class="col-w48r col-amber">${fmt(d.tokens_total ?? 0)}</span>` +
    `<span class="col-w48r col-blue">${d.latencia_ms ?? 0}</span>` +
    `<span class="col-w48r" style="color:${scoreColor}">${score.toFixed(3)}</span>` +
    `<span class="col-flex" title="${esc(pergunta)}">${esc(pergunta)}</span>`;

  list.insertBefore(item, list.firstChild);
  while (list.children.length > 100) list.removeChild(list.lastChild);
}

// ── Executar query (POST → SSE via fetch streaming) ───────────────────────
function runQuery() {
  const input   = $('query-input');
  const pergunta = input?.value.trim() ?? '';
  if (!pergunta) return;

  const btn = $('btn-query');
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }

  // Força tab pipeline
  const leftTabs = document.querySelectorAll('#tabs-left .tab');
  leftTabs.forEach(t => t.classList.remove('active'));
  const pipeTab = document.querySelector('#tabs-left [data-tab="pipeline"]');
  if (pipeTab) pipeTab.classList.add('active');
  ['pipeline','terminal','chunks'].forEach(k => {
    const el = $('tab-' + k);
    if (el) el.style.display = k === 'pipeline' ? 'block' : 'none';
  });

  buildSteps();

  fetch('/eval/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pergunta }),
  })
  .then(async res => {
    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() ?? '';

      for (const block of lines) {
        if (!block.startsWith('data: ')) continue;
        try {
          const d = JSON.parse(block.slice(6));
          handleQueryEvent(d, pergunta);
        } catch { /* JSON parcial */ }
      }
    }
  })
  .catch(err => {
    appendLog({ ts: now(), level: 'ERROR', name: 'query', msg: String(err), cor: 'var(--red)' });
  })
  .finally(() => {
    if (btn) { btn.disabled = false; btn.textContent = '↵ Executar'; }
  });
}

function handleQueryEvent(d, pergunta) {
  switch (d.tipo) {
    case 'step_start':  stepRunning(d.step); break;
    case 'step_result': stepDone(d.step, d.resultado, d.badge, d.ms); break;
    case 'chunk_rag':   addChunk(d); break;
    case 'resposta':    showResposta(d.texto, d.fonte, d.tokens); break;
    case 'metricas':    updateMetrics(d); addHistorico(d, pergunta); break;
    case 'erro':
      appendLog({ ts: now(), level: 'ERROR', name: 'pipeline', msg: d.msg, cor: 'var(--red)' });
      break;
  }
}

// ── Calendário ────────────────────────────────────────────────────────────
async function loadEventos() {
  const list   = $('eventos-list');
  const header = $('eventos-header');

  try {
    const r    = await fetch('/eval/eventos');
    const data = await r.json();

    if (header) header.textContent =
      `próximos 30 dias — ${data.eventos.length} evento(s)`;

    if (!list) return;

    if (!data.eventos.length) {
      list.innerHTML =
        '<div class="empty-state">Nenhum evento encontrado<br>' +
        '<small>O calendário foi ingerido no Redis?</small></div>';
      return;
    }

    list.innerHTML = data.eventos.map(e => {
      const dias = e.dias_restantes;
      const cor  =
        dias === 0 ? 'var(--red)' :
        dias <= 3  ? 'var(--amber)' :
        dias <= 7  ? 'var(--accent)' : 'var(--text)';
      const catCls = 'cat-' + (e.categoria ?? 'outro');
      const diasTxt = dias === 0 ? 'HOJE' : dias + 'd';

      return (
        `<div class="evento-item">` +
          `<div class="evento-dias" style="color:${cor}">` +
            `${diasTxt}<small>restam</small>` +
          `</div>` +
          `<div class="evento-info">` +
            `<div class="evento-nome">${e.emoji} ${esc(e.nome)}</div>` +
            `<div class="evento-data">${e.data_inicio}` +
              `${e.data_fim && e.data_fim !== e.data_inicio ? ' → ' + e.data_fim : ''}` +
            `</div>` +
          `</div>` +
          `<span class="badge-cat ${catCls}">${e.categoria}</span>` +
        `</div>`
      );
    }).join('');

  } catch (err) {
    if (list) list.innerHTML = '<div class="empty-state">Erro ao carregar eventos</div>';
    if (header) header.textContent = 'erro ao carregar';
  }
}

// ── Full Eval ─────────────────────────────────────────────────────────────
async function runFullEval() {
  const btn = $('btn-full-eval');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Executando...'; }

  try {
    const r = await fetch('/eval/run-full', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ versao: 'live' }),
    });
    const d = await r.json();
    appendLog({
      ts: now(), level: 'INFO', name: 'eval',
      msg: 'Full eval enfileirado: ' + JSON.stringify(d),
      cor: 'var(--accent)',
    });
  } catch (err) {
    appendLog({ ts: now(), level: 'ERROR', name: 'eval', msg: String(err), cor: 'var(--red)' });
  } finally {
    setTimeout(() => {
      if (btn) { btn.disabled = false; btn.textContent = '▶ Full Eval'; }
    }, 3000);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────
const now = () => new Date().toLocaleTimeString('pt-BR');

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupTabs('#tabs-left',  ['pipeline','terminal','chunks'], 'tab-');
  setupTabs('#tabs-right', ['metrics','eventos','historico'], 'rtab-');

  $('query-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runQuery(); }
  });
  $('btn-query')?.addEventListener('click', runQuery);
  $('btn-full-eval')?.addEventListener('click', runFullEval);

  connectLogs();
  loadEventos();
  setInterval(loadEventos, 300_000);
});