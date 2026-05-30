// Usamos o THREAD_ID injetado pelo Jinja no HTML
const THREAD_ID = window.APP_CONFIG?.THREAD_ID || "unknown";

const state = {
  processing: false,
  msgCount:   0,
  routeCounts: {},
  currentStepLatencies: {},
  currentSource: null,
};

let latencyChart, routeChart;

/* ════════ CHARTS ════════ */
function initCharts() {
  const baseOpts = { responsive: true, plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#4a5568', font: { size: 9 } } }, y: { ticks: { color: '#4a5568', font: { size: 9 } } } } };

  latencyChart = new Chart(document.getElementById('chart-latency'), {
    type: 'bar',
    data: {
      labels: ['router', 'planner', 'dispatch', 'synthesis'],
      datasets: [{ label: 'ms', data: [0, 0, 0, 0], backgroundColor: ['#6b8cff55','#00d4aa55','#f59e0b55','#ff5c3555'], borderColor: ['#6b8cff', '#00d4aa', '#f59e0b', '#ff5c35'], borderWidth: 1 }]
    },
    options: { ...baseOpts, animation: { duration: 400 } }
  });

  routeChart = new Chart(document.getElementById('chart-routes'), {
    type: 'doughnut',
    data: {
      labels: [],
      datasets: [{ data: [], backgroundColor: ['#6b8cff55','#00d4aa55','#ff5c3555','#f59e0b55','#a855f755','#22d3a055'], borderColor: ['#6b8cff', '#00d4aa', '#ff5c35', '#f59e0b', '#a855f7', '#22d3a0'], borderWidth: 1 }]
    },
    options: { responsive: true, plugins: { legend: { display: true, labels: { color: '#4a5568', font: { size: 9 }, boxWidth: 10 } } }, animation: { duration: 400 } }
  });
}

function updateLatencyChart(latencies) {
  const steps = ['router','planner','dispatch','synthesis'];
  latencyChart.data.datasets[0].data = steps.map(s => latencies[s] || 0);
  latencyChart.update();
}

function updateRouteChart(route) {
  if (!route) return;
  state.routeCounts[route] = (state.routeCounts[route] || 0) + 1;
  routeChart.data.labels   = Object.keys(state.routeCounts);
  routeChart.data.datasets[0].data = Object.values(state.routeCounts);
  routeChart.update();
}

/* ════════ UI HELPERS ════════ */
window.switchTab = function(name, btn) {
  document.querySelectorAll('.cx-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.cx-tab-content').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
};

window.clearPipeline = function() {
  document.getElementById('cx-pipeline').innerHTML = `<div class="cx-pipeline-empty" id="pipeline-empty">Aguardando mensagem…</div>`;
};

window.clearChat = function() {
  document.getElementById('cx-messages').innerHTML = '';
};

function upsertStep(id, status, name, detail, ms, extra) {
  const empty = document.getElementById('pipeline-empty');
  if (empty) empty.remove();

  const container = document.getElementById('cx-pipeline');
  let el = document.getElementById('step-' + id);
  const icons = { running: '◎', ok: '✓', error: '✗', skip: '—' };

  if (!el) {
    el = document.createElement('div');
    el.id = 'step-' + id;
    container.appendChild(el);
  }
  
  el.className = 'cx-step ' + status;
  const msText = ms > 0 ? ms + 'ms' : '';
  const extraHtml = extra ? `<div style="color:var(--accent2);font-size:9px;margin-top:2px">${extra}</div>` : '';

  el.innerHTML = `
    <div class="cx-step-icon">${icons[status] || '◎'}</div>
    <div class="cx-step-body">
      <div class="cx-step-name">${name}</div>
      <div class="cx-step-detail">${detail}</div>
      ${extraHtml}
      ${msText ? `<div class="cx-step-ms">${msText}</div>` : ''}
    </div>`;

  container.scrollTop = container.scrollHeight;
}

function addMessage(role, text, meta) {
  const wrap = document.getElementById('cx-messages');
  const typingIndicator = document.getElementById('cx-typing');
  
  const row = document.createElement('div');
  row.className = 'cx-msg ' + role;

  // Renderização segura de HTML (evita XSS)
  const safeText = text
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
    
  row.innerHTML = `
    <div class="cx-avatar">${role === 'user' ? 'ADM' : 'OS'}</div>
    <div>
      <div class="cx-bubble">${safeText}</div>
      <div class="cx-meta">${meta || ''}</div>
    </div>`;

  // MÁGICA SEGURA: Em vez de inserir antes do typing (que pode estar fora da div), 
  // nós apenas adicionamos ao final da lista de mensagens (wrap).
  // O indicador de 'typing' deve estar DE FORA da div 'cx-messages' no HTML.
  wrap.appendChild(row); 
  
  // Rola para o final
  wrap.scrollTop = wrap.scrollHeight;
} 

function showTyping() { document.getElementById('cx-typing').classList.add('active'); }
function hideTyping() { document.getElementById('cx-typing').classList.remove('active'); }

/* ════════ CORE: SSE HANDLER ════════ */
window.sendMessage = function(overrideMsg) {
  if (state.processing) return;

  const input = document.getElementById('cx-input');
  const msg   = (overrideMsg || input.value).trim();
  if (!msg) return;

  input.value = '';
  state.processing = true;
  state.msgCount++;
  document.getElementById('cx-send').disabled = true;

  addMessage('user', msg, 'agora');
  showTyping();

  // ⚠️ VERIFIQUE SE ESTA ROTA /hub/chat/stream É A MESMA DO SEU BACKEND (hub.py)
  const url = `/hub/chat/stream?msg=${encodeURIComponent(msg)}&thread_id=${encodeURIComponent(THREAD_ID)}`;
  
  console.log("Iniciando conexão com:", url); // Log para debug no F12
  const es = new EventSource(url);
  state.currentSource = es;

  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.type === 'step') {
        upsertStep('msg' + state.msgCount + '_' + d.step, d.status, d.step, d.detail, d.ms, d.rota || d.plan_id);
        if (d.ms > 0) state.currentStepLatencies[d.step] = d.ms;
      } 
      else if (d.type === 'response') {
        hideTyping();
        addMessage('bot', d.text, d.rota || '');
      } 
      else if (d.type === 'metrics') {
        if (d.total_ms) document.getElementById('stat-total-ms').textContent = d.total_ms + 'ms';
        if (d.rota) { document.getElementById('stat-rota').textContent = d.rota; updateRouteChart(d.rota); }
        if (d.workers) document.getElementById('stat-workers').textContent = d.workers;
        document.getElementById('stat-msgs').textContent = state.msgCount;
        updateLatencyChart(state.currentStepLatencies);
      } 
      else if (d.type === 'error') {
        hideTyping();
        upsertStep('err', 'error', 'erro', d.msg, 0, null);
        addMessage('bot', '❌ Erro interno.', 'sistema');
      } 
      else if (d.type === 'done') {
        es.close();
        state.processing = false;
        document.getElementById('cx-send').disabled = false;
      }
    } catch(err) {
      console.error("Erro processando SSE:", err);
    }
  };

  // Se o backend falhar completamente (ex: Erro 500 ou Server Offline)
  es.onerror = () => {
    console.error("Conexão SSE caiu ou falhou.");
    es.close();
    hideTyping();
    state.processing = false;
    document.getElementById('cx-send').disabled = false;
    upsertStep('conn_err', 'error', 'conexão', 'Falha no servidor. Verifique os logs do FastAPI.', 0, null);
  };
};

window.quickSend = function(text) { window.sendMessage(text); };
window.runCustom = function() {
  const v = document.getElementById('custom-query').value.trim();
  if (v) { window.quickSend(v); document.getElementById('custom-query').value = ''; }
};

document.getElementById('cx-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window.sendMessage(); }
});

document.addEventListener('DOMContentLoaded', () => initCharts());