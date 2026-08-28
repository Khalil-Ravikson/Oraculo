/* chat.js — simulador de chat / debugger de pipeline (Plano B §G).
   Move o chat-debugger.js para cá: sem emoji, sem handlers inline (delegação
   por data-attr), Chart.js do bundle local. Contrato SSE de /hub/chat/stream
   preservado: eventos `step` / `response` / `metrics` / `error` / `done`. */

const THREAD_ID = document.querySelector('.cx-root')?.dataset.threadId || 'unknown';

const state = {
  processing: false,
  msgCount: 0,
  routeCounts: {},
  stepLatencies: {},
  source: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

/* ── Ícones de status (Lucide) ───────────────────────────────────── */
const ICON = {
  running: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.2-8.5"/></svg>',
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  skip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/></svg>',
};

/* ── Charts ──────────────────────────────────────────────────────── */
const CHART_FONT = { family: 'Geist Mono, ui-monospace, monospace', size: 9 };
const AXIS = { ticks: { color: '#7b8394', font: CHART_FONT }, grid: { color: 'rgba(255,255,255,.05)' } };
const SERIES = ['#d97a3f', '#3ba55c', '#c9982e', '#5a8dee', '#9a7fd4', '#4bb88a'];
let latencyChart, routeChart;

function initCharts() {
  latencyChart = new Chart($('chart-latency'), {
    type: 'bar',
    data: {
      labels: ['router', 'planner', 'dispatch', 'synthesis'],
      datasets: [{ data: [0, 0, 0, 0], backgroundColor: SERIES.map((c) => c + '66'), borderColor: SERIES, borderWidth: 1 }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: AXIS, y: AXIS }, animation: { duration: 300 } },
  });
  routeChart = new Chart($('chart-routes'), {
    type: 'doughnut',
    data: { labels: [], datasets: [{ data: [], backgroundColor: SERIES.map((c) => c + 'aa'), borderColor: '#12151a', borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: '#9aa2b1', font: CHART_FONT, boxWidth: 10 } } }, animation: { duration: 300 } },
  });
}

function updateLatencyChart() {
  const steps = ['router', 'planner', 'dispatch', 'synthesis'];
  latencyChart.data.datasets[0].data = steps.map((s) => state.stepLatencies[s] || 0);
  latencyChart.update();
}

function updateRouteChart(route) {
  if (!route) return;
  state.routeCounts[route] = (state.routeCounts[route] || 0) + 1;
  routeChart.data.labels = Object.keys(state.routeCounts);
  routeChart.data.datasets[0].data = Object.values(state.routeCounts);
  routeChart.update();
}

/* ── Abas / limpeza ──────────────────────────────────────────────── */
function switchTab(name) {
  document.querySelectorAll('.cx-tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.cx-tab-content').forEach((c) => c.classList.toggle('active', c.id === 'tab-' + name));
}
function clearPipeline() {
  $('cx-pipeline').innerHTML = '<div class="cx-pipeline-empty" id="pipeline-empty">Aguardando mensagem…</div>';
}
function clearChat() { $('cx-messages').innerHTML = ''; }

async function apiAction(url, method, label) {
  try {
    const r = await fetch(url, { method, credentials: 'same-origin' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { addMessage('bot', `${d.detail || label + ' falhou (' + r.status + ')'}`, 'sistema'); return; }
    const extra = typeof d.deleted === 'number' ? ` (${d.deleted} entradas removidas)` : '';
    addMessage('bot', `${label}${extra}`, 'sistema');
  } catch (e) {
    addMessage('bot', `${label} falhou: ${e.message}`, 'sistema');
  }
}

/* ── Pipeline ────────────────────────────────────────────────────── */
function upsertStep(id, status, name, detail, ms, extra) {
  $('pipeline-empty')?.remove();
  const container = $('cx-pipeline');
  let el = $('step-' + id);
  if (!el) {
    el = document.createElement('div');
    el.id = 'step-' + id;
    container.appendChild(el);
  }
  el.className = 'cx-step ' + status;
  const msText = ms > 0 ? `${ms}ms` : '';
  el.innerHTML = `
    <span class="cx-step__icon">${ICON[status] || ICON.running}</span>
    <div class="cx-step__body">
      <div class="cx-step__name">${esc(name)}</div>
      <div class="cx-step__detail">${esc(detail)}</div>
      ${extra ? `<div class="cx-step__extra">${esc(extra)}</div>` : ''}
      ${msText ? `<div class="cx-step__ms">${msText}</div>` : ''}
    </div>`;
  container.scrollTop = container.scrollHeight;
}

/* ── Mensagens ───────────────────────────────────────────────────── */
function addMessage(role, text, meta, buttons = []) {
  const wrap = $('cx-messages');
  const isUser = role === 'user';
  const row = document.createElement('div');
  row.className = 'cx-msg ' + (isUser ? 'cx-msg--user' : 'cx-msg--bot');

  const safeText = esc(text).replace(/\n/g, '<br>');
  const btnHtml = (buttons || []).length
    ? `<div class="cx-action-buttons">${buttons.map((b) => `<button type="button" class="btn btn--sm" data-send-value="${esc(b.value)}">${esc(b.label)}</button>`).join('')}</div>`
    : '';
  const badge = meta ? `<span class="badge badge--neutral">${esc(meta)}</span>` : '';

  row.innerHTML = `
    <div class="cx-avatar">${isUser ? 'ADM' : 'OS'}</div>
    <div>
      <div class="cx-bubble">${safeText}${btnHtml}</div>
      <div class="cx-meta">${badge}</div>
    </div>`;
  wrap.appendChild(row);
  wrap.scrollTop = wrap.scrollHeight;
}

function setProcessing(on) {
  state.processing = on;
  $('cx-send').disabled = on;
  $('cx-progress').classList.toggle('active', on);
  $('cx-say').hidden = !on;
}

/* A resposta chegou mas o stream ainda emite metrics/done — para a barra e o
   aviso, mas mantém o envio travado até `done` (evita duplo submit que bate no
   lock de sessão do backend). */
function settleVisual() {
  $('cx-progress').classList.remove('active');
  $('cx-say').hidden = true;
}

/* ── Envio + SSE ─────────────────────────────────────────────────── */
function sendMessage(overrideMsg) {
  if (state.processing) return;
  const input = $('cx-input');
  const msg = (overrideMsg || input.value).trim();
  if (!msg) return;

  input.value = '';
  state.msgCount++;
  setProcessing(true);
  addMessage('user', msg, 'agora');

  const url = `/hub/chat/stream?msg=${encodeURIComponent(msg)}&thread_id=${encodeURIComponent(THREAD_ID)}`;
  const es = new EventSource(url);
  state.source = es;

  es.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }

    if (d.type === 'step') {
      upsertStep(`msg${state.msgCount}_${d.step}`, d.status, d.step, d.detail, d.ms, d.rota || d.plan_id);
      if (d.ms > 0) state.stepLatencies[d.step] = d.ms;
    } else if (d.type === 'response') {
      settleVisual();
      const badge = d.status === 'hitl_pending' ? 'HITL' : (d.rota || '');
      addMessage('bot', d.text, badge, d.action_buttons || []);
    } else if (d.type === 'metrics') {
      if (d.total_ms) $('stat-total-ms').textContent = d.total_ms + 'ms';
      if (d.rota) { $('stat-rota').textContent = d.rota; updateRouteChart(d.rota); }
      if (d.workers) $('stat-workers').textContent = d.workers;
      $('stat-msgs').textContent = state.msgCount;
      updateLatencyChart();
    } else if (d.type === 'error') {
      settleVisual();
      upsertStep('err', 'error', 'erro', d.msg, 0, null);
      addMessage('bot', 'Erro interno ao processar.', 'sistema');
    } else if (d.type === 'done') {
      es.close();
      setProcessing(false);
    }
  };

  es.onerror = () => {
    es.close();
    setProcessing(false);
    upsertStep('conn_err', 'error', 'conexão', 'Falha no servidor. Verifique os logs do FastAPI.', 0, null);
  };
}

/* ── Wiring ──────────────────────────────────────────────────────── */
document.querySelectorAll('.cx-tab').forEach((t) => (t.onclick = () => switchTab(t.dataset.tab)));

$('cx-send').onclick = () => sendMessage();
$('cx-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

$('cx-messages').addEventListener('click', (e) => {
  const b = e.target.closest('[data-send-value]');
  if (b) sendMessage(b.dataset.sendValue);
});

document.querySelectorAll('[data-send]').forEach((b) => (b.onclick = () => sendMessage(b.dataset.send)));
$('cmd-clear-cache').onclick = () => apiAction('/api/admin/cache', 'DELETE', 'Cache semântico limpo');
$('cmd-clear-pipeline').onclick = clearPipeline;
$('cmd-clear-chat').onclick = clearChat;
$('cmd-run').onclick = () => {
  const v = $('custom-query').value.trim();
  if (v) { sendMessage(v); $('custom-query').value = ''; }
};
$('custom-query').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('cmd-run').click(); });

initCharts();
