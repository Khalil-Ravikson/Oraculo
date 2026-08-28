/* eval.js — dashboard de avaliação RAG (Plano B §G).
   Move o <script> inline de eval.html para cá. Chart.js vem do bundle local
   (static/js/vendor/chart.min.js) — sem CDN. Script clássico (não módulo)
   porque usa o global `Chart` do bundle. Os presets de custo são ligados por
   `data-preset` + listener delegado (sem handlers inline no template). */

const C = {
  signal: '#d97a3f', ok: '#3ba55c', warn: '#c9982e', danger: '#d9483f',
  blue: '#5a8dee', purple: '#9a7fd4', ink400: '#9aa2b1', ink500: '#7b8394',
  line: '#262b33', paper: '#e8eaed',
};
const CHART_FONT = { family: 'Geist Mono, ui-monospace, monospace', size: 10 };

const evalState = {
  results: [],
  charts: {},
  inputCostPer1M: 0.075,
  outputCostPer1M: 0.30,
  sseSource: null,
};

const $ = (id) => document.getElementById(id);

/* ── Abas ────────────────────────────────────────────────────────── */
document.querySelectorAll('.eval-tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.eval-tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.tab-section').forEach((s) => (s.style.display = 'none'));
    $(tab.dataset.target).style.display = 'block';
  });
});

/* ── Gaveta de custo ─────────────────────────────────────────────── */
const cogBtn = $('btn-cost-settings');
const drawer = $('cost-config-drawer');
cogBtn.addEventListener('click', (e) => { e.stopPropagation(); drawer.classList.toggle('visible'); });
document.addEventListener('click', (e) => {
  if (!drawer.contains(e.target) && e.target !== cogBtn && !cogBtn.contains(e.target)) {
    drawer.classList.remove('visible');
  }
});

function setPresetPricing(inp, out, label) {
  $('cfg-cost-input').value = inp;
  $('cfg-cost-output').value = out;
  $('lbl-cost-pricing').textContent = label;
}
document.querySelectorAll('[data-preset]').forEach((b) => {
  b.addEventListener('click', () => {
    const [i, o, l] = b.dataset.preset.split('|');
    setPresetPricing(Number(i), Number(o), l);
  });
});

$('btn-apply-cost').addEventListener('click', () => {
  evalState.inputCostPer1M = parseFloat($('cfg-cost-input').value) || 0;
  evalState.outputCostPer1M = parseFloat($('cfg-cost-output').value) || 0;
  drawer.classList.remove('visible');
  recalculateCosts();
});

document.querySelectorAll('[data-stub]').forEach((b) => {
  b.addEventListener('click', () => alert(b.dataset.stub));
});

/* ── Recalcular custos ───────────────────────────────────────────── */
function recalculateCosts() {
  let total = 0;
  evalState.results.forEach((res) => {
    const q = (res.tokens_entrada * evalState.inputCostPer1M + res.tokens_saida * evalState.outputCostPer1M) / 1e6;
    res.cost_usd = q;
    total += q;
    const td = $(`td-cost-${res.id}`);
    if (td) td.textContent = `$${q.toFixed(5)}`;
  });
  $('s-cost').textContent = `$${total.toFixed(5)}`;
  updateCategoryChart();
}

/* ── Rodar avaliação ─────────────────────────────────────────────── */
const btnRun = $('btn-iniciar-eval');
btnRun.addEventListener('click', async () => {
  btnRun.disabled = true;
  $('progress-card').classList.add('visible');
  $('progress-fill').style.width = '0%';
  $('progress-num').textContent = '0 / 30';
  $('progress-label').textContent = 'Iniciando avaliação…';
  $('eval-results-tbody').innerHTML = '';
  evalState.results = [];

  try {
    const r = await fetch('/hub/eval/run', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    const d = await r.json();
    if (!r.ok) { alert(d.error || 'Erro ao iniciar'); btnRun.disabled = false; return; }
    connectStream();
  } catch (err) {
    alert('Erro de conexão: ' + err.message);
    btnRun.disabled = false;
  }
});

function connectStream() {
  if (evalState.sseSource) evalState.sseSource.close();
  const src = new EventSource('/hub/eval/stream');
  evalState.sseSource = src;

  src.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'ping') return;

    if (data.type === 'start') {
      $('progress-label').textContent = 'Executando avaliação RAG…';
      $('progress-num').textContent = `0 / ${data.total}`;
    } else if (data.type === 'progress') {
      $('progress-num').textContent = `${data.current} / ${data.total}`;
      $('progress-fill').style.width = `${(data.current / data.total) * 100}%`;
      $('progress-question').textContent = `Pergunta: "${data.question}"`;
    } else if (data.type === 'result') {
      const item = {
        id: data.id, category: data.category || 'GERAL', question: data.question,
        answer: data.answer || 'Sem resposta', route_detected: data.route_detected || 'GERAL',
        hit_rate: data.hit_rate || 0, mrr: data.mrr || 0, crag: data.crag || 0,
        faithfulness: data.faithfulness || 0, relevancy: data.relevancy || 0,
        latency_ms: data.latency_ms || 0, tokens_entrada: data.tokens_entrada || 0,
        tokens_saida: data.tokens_saida || 0, tokens_total: data.tokens_total || 0,
        cost_usd: data.cost_usd || 0, memory_mb: data.memory_mb || 0,
        worker_name: data.worker_name || 'worker_synthesis', error: data.error || '',
      };
      item.cost_usd = (item.tokens_entrada * evalState.inputCostPer1M + item.tokens_saida * evalState.outputCostPer1M) / 1e6;
      evalState.results.push(item);
      appendResultToTable(item);
      updateLiveCharts();
    } else if (data.type === 'done') {
      $('progress-label').textContent = 'Avaliação concluída.';
      $('progress-fill').style.width = '100%';
      btnRun.disabled = false;
      src.close();
      $('s-hit-rate').textContent = data.avg_hit.toFixed(3);
      $('s-faithfulness').textContent = data.avg_faith.toFixed(3);
      $('s-relevancy').textContent = data.avg_relev.toFixed(3);
      $('s-latency').textContent = `${data.avg_lat_ms} ms`;
      recalculateCosts();
    }
  };

  src.onerror = () => {
    $('progress-label').textContent = 'Conexão perdida. Rode novamente.';
    src.close();
    btnRun.disabled = false;
  };
}

/* ── Tabela ──────────────────────────────────────────────────────── */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const scoreClass = (v, hi, mid) => (v >= hi ? 'score--high' : v >= mid ? 'score--mid' : 'score--low');

function appendResultToTable(res) {
  const tbody = $('eval-results-tbody');
  if (evalState.results.length === 1) tbody.innerHTML = '';

  const row = document.createElement('tr');
  row.id = `row-${res.id}`;
  row.addEventListener('click', () => {
    const d = $(`detail-row-${res.id}`);
    if (d) d.classList.toggle('expanded');
  });
  const routeOk = res.category.toUpperCase() === res.route_detected.toUpperCase();
  row.innerHTML = `
    <td class="num">${res.id}</td>
    <td class="q-cell">${esc(res.question)}</td>
    <td><span class="route-tag route-tag--ok">${esc(res.category)}</span></td>
    <td><span class="route-tag ${routeOk ? 'route-tag--ok' : 'route-tag--bad'}">${esc(res.route_detected)}</span></td>
    <td class="score ${scoreClass(res.hit_rate, 0.8, 0.4)}">${res.hit_rate.toFixed(2)}</td>
    <td class="num">${res.mrr.toFixed(2)}</td>
    <td class="score ${scoreClass(res.faithfulness, 0.8, 0.5)}">${res.faithfulness.toFixed(2)}</td>
    <td class="score ${scoreClass(res.relevancy, 0.8, 0.5)}">${res.relevancy.toFixed(2)}</td>
    <td class="num">${res.latency_ms} ms</td>
    <td class="num" id="td-cost-${res.id}">$${res.cost_usd.toFixed(5)}</td>`;

  const detail = document.createElement('tr');
  detail.id = `detail-row-${res.id}`;
  detail.className = 'detail-row';
  detail.innerHTML = `
    <td colspan="10"><div class="detail-content">
      <div class="detail-block"><span class="detail-block__lbl">Pergunta</span><div class="detail-block__val">${esc(res.question)}</div></div>
      <div class="detail-block"><span class="detail-block__lbl">Resposta da LLM</span><div class="detail-block__val">${esc(res.answer)}</div></div>
      <div class="detail-meta">
        <span>worker: <strong>${esc(res.worker_name)}</strong></span>
        <span>tokens in: <strong>${res.tokens_entrada}</strong></span>
        <span>tokens out: <strong>${res.tokens_saida}</strong></span>
        <span>tokens total: <strong>${res.tokens_total}</strong></span>
        <span>memória: <strong>${res.memory_mb.toFixed(1)} MB</strong></span>
      </div>
    </div></td>`;

  tbody.appendChild(row);
  tbody.appendChild(detail);
}

/* ── Gráficos ────────────────────────────────────────────────────── */
const gridColor = 'rgba(255,255,255,.05)';
const axis = { grid: { color: gridColor }, ticks: { color: C.ink500, font: CHART_FONT } };

function initCharts() {
  evalState.charts.latency = new Chart($('chart-latency'), {
    type: 'bar',
    data: { labels: [], datasets: [{ label: 'Latência (ms)', data: [], backgroundColor: C.warn + '66', borderColor: C.warn, borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: axis, x: { ...axis, grid: { display: false } } }, plugins: { legend: { display: false } } },
  });

  evalState.charts.routes = new Chart($('chart-routes'), {
    type: 'doughnut',
    data: { labels: [], datasets: [{ data: [], backgroundColor: [C.ok, C.blue, C.purple, C.warn, C.danger, C.signal].map((c) => c + 'aa'), borderColor: '#12151a', borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { color: C.ink400, font: CHART_FONT } } } },
  });

  evalState.charts.workers = new Chart($('chart-workers'), {
    type: 'bar',
    data: { labels: ['worker_rag', 'worker_synthesis', 'worker_media', 'worker_sigaa'], datasets: [{ label: 'Chamadas', data: [0, 0, 0, 0], backgroundColor: C.ok + '66', borderColor: C.ok, borderWidth: 1 }] },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, scales: { x: axis, y: { ...axis, grid: { display: false } } }, plugins: { legend: { display: false } } },
  });

  evalState.charts.categories = new Chart($('chart-metrics-categories'), {
    type: 'bar',
    data: {
      labels: ['CALENDARIO', 'EDITAL', 'CONTATOS', 'GERAL'],
      datasets: [
        { label: 'Custo médio ($)', data: [0, 0, 0, 0], backgroundColor: C.danger + '66', borderColor: C.danger, borderWidth: 1, yAxisID: 'yCost' },
        { label: 'Relevância média', data: [0, 0, 0, 0], backgroundColor: C.blue + '66', borderColor: C.blue, borderWidth: 1, yAxisID: 'yScore' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        yScore: { type: 'linear', position: 'left', min: 0, max: 1, ...axis },
        yCost: { type: 'linear', position: 'right', grid: { display: false }, ticks: { color: C.danger, font: CHART_FONT } },
        x: { ticks: { color: C.ink500, font: CHART_FONT } },
      },
      plugins: { legend: { labels: { color: C.ink400, font: CHART_FONT } } },
    },
  });
}

function updateLiveCharts() {
  const r = evalState.results;
  evalState.charts.latency.data.labels = r.map((x) => x.id);
  evalState.charts.latency.data.datasets[0].data = r.map((x) => x.latency_ms);
  evalState.charts.latency.update();

  const routes = {};
  r.forEach((x) => (routes[x.route_detected] = (routes[x.route_detected] || 0) + 1));
  evalState.charts.routes.data.labels = Object.keys(routes);
  evalState.charts.routes.data.datasets[0].data = Object.values(routes);
  evalState.charts.routes.update();

  const w = { worker_rag: 0, worker_synthesis: 0, worker_media: 0, worker_sigaa: 0 };
  r.forEach((x) => { if (w[x.worker_name] !== undefined) w[x.worker_name]++; });
  evalState.charts.workers.data.datasets[0].data = [w.worker_rag, w.worker_synthesis, w.worker_media, w.worker_sigaa];
  evalState.charts.workers.update();

  updateCategoryChart();
}

function updateCategoryChart() {
  const cats = ['CALENDARIO', 'EDITAL', 'CONTATOS', 'GERAL'];
  const acc = {};
  cats.forEach((c) => (acc[c] = { cost: 0, relevancy: 0, count: 0 }));
  evalState.results.forEach((r) => {
    const c = r.category.toUpperCase();
    if (acc[c]) { acc[c].cost += r.cost_usd; acc[c].relevancy += r.relevancy; acc[c].count++; }
  });
  evalState.charts.categories.data.datasets[0].data = cats.map((c) => (acc[c].count ? acc[c].cost / acc[c].count : 0));
  evalState.charts.categories.data.datasets[1].data = cats.map((c) => (acc[c].count ? acc[c].relevancy / acc[c].count : 0));
  evalState.charts.categories.update();
}

/* ── Resultados anteriores ───────────────────────────────────────── */
async function loadInitialResults() {
  try {
    const r = await fetch('/hub/eval/results');
    const d = await r.json();
    if (!d.results || !d.results.length) return;
    const last = d.results[0];
    $('s-hit-rate').textContent = last.avg_hit_rate.toFixed(3);
    $('s-faithfulness').textContent = last.avg_faithfulness.toFixed(3);
    $('s-relevancy').textContent = last.avg_relevancy.toFixed(3);
    $('s-latency').textContent = `${last.avg_latency_ms} ms`;
    $('eval-results-tbody').innerHTML = '';
    evalState.results = last.results.map((x) => ({ ...x, category: x.category || 'GERAL', cost_usd: x.cost_usd || 0 }));
    evalState.results.forEach(appendResultToTable);
    recalculateCosts();
    updateLiveCharts();
  } catch (err) {
    console.warn('eval: sem resultados anteriores', err);
  }
}

initCharts();
loadInitialResults();
