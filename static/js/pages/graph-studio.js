/* graph-studio.js — área de desenho do fluxo (Hub v2, Konva.js vendorado).
   Clique num componente da paleta pra adicionar; clique numa porta de saída e
   depois numa de entrada pra ligar; arraste pra reposicionar; duplo-clique
   remove. Undo/redo, minimapa, exportar/importar. Validação real (tipos + sem
   ciclos) roda no servidor ao salvar. */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { Glossario } from '/static/js/core/glossario.js';

const nodeLabel = (id) => Glossario.rotulo('node:' + id, (nodesById[id]?.metadata?.name) || id);

const NODE_WIDTH = 180;
const NODE_HEADER = 28;
const PORT_ROW_H = 20;
const COLOR_PORT = '#7b8394';
const COLOR_PORT_ARMED = '#d97a3f';
const COLOR_EDGE = '#d97a3f';

let stage, layer;
let nodesById = {};      // node_id -> metadata do registry (portas, tipos)
let canvasNodes = {};    // node_id -> { group, config }
let canvasEdges = [];    // [{ source_node, source_port, target_node, target_port, line }]
let armedPort = null;    // { node_id, port_name, direction, circle }
let nodeStatus = {};     // node_id -> 'running'|'ok'|'error'|'skip' (último teste)

function initStage() {
  const container = document.getElementById('canvas');
  stage = new Konva.Stage({ container: 'canvas', width: container.clientWidth || 900, height: 640, draggable: true });
  layer = new Konva.Layer();
  stage.add(layer);
  stage.on('wheel', (e) => {
    e.evt.preventDefault();
    zoom(e.evt.deltaY > 0 ? 0.9 : 1.1);
  });
}

function zoom(fator) {
  const novo = Math.max(0.3, Math.min(2.5, stage.scaleX() * fator));
  stage.scale({ x: novo, y: novo });
  stage.batchDraw();
}

function zoomFit() {
  stage.scale({ x: 1, y: 1 });
  stage.position({ x: 0, y: 0 });
  stage.batchDraw();
}

function atualizarValidade() {
  const el = document.getElementById('validity');
  const nn = Object.keys(canvasNodes).length;
  const ne = canvasEdges.length;
  if (nn === 0) { el.className = 'badge badge--unknown'; el.textContent = 'vazio'; return; }
  const soltos = Object.keys(canvasNodes).filter((id) =>
    !canvasEdges.some((e) => e.source_node === id || e.target_node === id));
  if (nn > 1 && soltos.length) {
    el.className = 'badge badge--warn';
    el.textContent = `${soltos.length} componente(s) sem ligação`;
    return;
  }
  el.className = 'badge badge--neutral';
  el.textContent = `${nn} componente${nn > 1 ? 's' : ''} · ${ne} ligaç${ne === 1 ? 'ão' : 'ões'}`;
}

// ── Undo / redo ───────────────────────────────────────────────────────────
let historico = [];
let historicoPos = -1;
let restaurando = false;

function pushHistory() {
  if (restaurando) return;
  historico = historico.slice(0, historicoPos + 1);
  historico.push(JSON.stringify(exportTopology()));
  if (historico.length > 50) historico.shift();
  historicoPos = historico.length - 1;
  sincronizarBotoesHistorico();
}

function restaurar(pos) {
  if (pos < 0 || pos >= historico.length) return;
  historicoPos = pos;
  restaurando = true;
  loadTopology(JSON.parse(historico[pos]));
  restaurando = false;
  atualizarValidade();
  sincronizarBotoesHistorico();
}

function sincronizarBotoesHistorico() {
  const u = document.getElementById('btn-undo');
  const r = document.getElementById('btn-redo');
  if (u) u.disabled = historicoPos <= 0;
  if (r) r.disabled = historicoPos >= historico.length - 1;
}

// ── Minimapa ──────────────────────────────────────────────────────────────
function renderMinimap() {
  const svg = document.getElementById('minimap');
  if (!svg) return;
  const ids = Object.keys(canvasNodes);
  if (!ids.length) { svg.innerHTML = ''; return; }
  const pts = ids.map((id) => ({ id, x: canvasNodes[id].group.x(), y: canvasNodes[id].group.y() }));
  const minX = Math.min(...pts.map((p) => p.x)) - 20;
  const minY = Math.min(...pts.map((p) => p.y)) - 20;
  const maxX = Math.max(...pts.map((p) => p.x)) + NODE_WIDTH + 20;
  const maxY = Math.max(...pts.map((p) => p.y)) + 120;
  const sx = 160 / Math.max(maxX - minX, 1);
  const sy = 100 / Math.max(maxY - minY, 1);
  const s = Math.min(sx, sy);
  const edges = canvasEdges.map((e) => {
    const a = canvasNodes[e.source_node]?.group, b = canvasNodes[e.target_node]?.group;
    if (!a || !b) return '';
    return `<line x1="${(a.x() - minX) * s + NODE_WIDTH * s}" y1="${(a.y() - minY) * s + 10}" x2="${(b.x() - minX) * s}" y2="${(b.y() - minY) * s + 10}" stroke="#d97a3f" stroke-width="0.7"/>`;
  }).join('');
  const rects = pts.map((p) =>
    `<rect x="${(p.x - minX) * s}" y="${(p.y - minY) * s}" width="${NODE_WIDTH * s}" height="${Math.max(6, 40 * s)}" rx="1.5" fill="#1b1f26" stroke="#333a44" stroke-width="0.5"/>`).join('');
  svg.innerHTML = edges + rects;
}

function portMetaOf(circle) {
  return circle.getAttr('portMeta');
}

function buildNodeGroup(nodeId, x, y) {
  const meta = nodesById[nodeId];
  const rows = Math.max(meta.input_ports.length, meta.output_ports.length, 1);
  const height = NODE_HEADER + rows * PORT_ROW_H + 10;

  const group = new Konva.Group({ x, y, draggable: true });
  group.setAttr('nodeId', nodeId);

  group.add(new Konva.Rect({
    width: NODE_WIDTH, height, fill: '#12151a', stroke: '#262b33', strokeWidth: 1, cornerRadius: 8,
  }));

  group.add(new Konva.Text({
    text: nodeLabel(nodeId), x: 8, y: 8, fontSize: 12,
    fontFamily: 'Geist, system-ui, sans-serif', fontStyle: '500', fill: '#e8eaed',
    width: NODE_WIDTH - 16, ellipsis: true, wrap: 'none',
  }));

  const addPort = (portDef, index, direction) => {
    const py = NODE_HEADER + index * PORT_ROW_H + 10;
    const cx = direction === 'input' ? 0 : NODE_WIDTH;
    const circle = new Konva.Circle({ x: cx, y: py, radius: 5, fill: COLOR_PORT });
    circle.setAttr('portMeta', { node_id: nodeId, port_name: portDef.name, type: portDef.type, direction });
    circle.on('click tap', onPortClick);
    circle.on('mouseenter', () => { stage.container().style.cursor = 'pointer'; });
    circle.on('mouseleave', () => { stage.container().style.cursor = 'default'; });
    group.add(circle);

    const label = new Konva.Text({
      text: portDef.name, y: py - 6, fontSize: 10, fontFamily: 'monospace', fill: '#7b8394',
      x: direction === 'input' ? 8 : undefined,
      width: direction === 'output' ? NODE_WIDTH - 8 : undefined,
      align: direction === 'output' ? 'right' : 'left',
    });
    if (direction === 'output') label.x(0);
    group.add(label);
  };

  meta.input_ports.forEach((p, i) => addPort(p, i, 'input'));
  meta.output_ports.forEach((p, i) => addPort(p, i, 'output'));

  group.on('dragmove', redrawEdges);
  group.on('dragend', () => { pushHistory(); renderMinimap(); });
  group.on('dblclick dbltap', (evt) => {
    // só remove se o alvo do duplo-clique for o retângulo/fundo, não uma porta
    if (evt.target.getAttr('portMeta')) return;
    removeNode(nodeId);
    pushHistory();
  });
  group.on('click tap', (evt) => {
    if (evt.target.getAttr('portMeta')) return;   // clique numa porta = ligação
    abrirPropsNo(nodeId);
  });

  layer.add(group);
  return group;
}

function addNodeToCanvas(nodeId, config = {}) {
  if (canvasNodes[nodeId]) { showToast(`"${nodeLabel(nodeId)}" já está na área`, 'error'); return; }
  const n = Object.keys(canvasNodes).length;
  const x = 40 + (n % 3) * 220;
  const y = 40 + Math.floor(n / 3) * 160;
  canvasNodes[nodeId] = { group: buildNodeGroup(nodeId, x, y), config: config || {} };
  layer.draw();
  if (!restaurando) { pushHistory(); renderMinimap(); }
}

function removeNode(nodeId) {
  if (!canvasNodes[nodeId]) return;
  canvasNodes[nodeId].group.destroy();
  delete canvasNodes[nodeId];
  canvasEdges = canvasEdges.filter(e => {
    const toca = e.source_node === nodeId || e.target_node === nodeId;
    if (toca) e.line.destroy();
    return !toca;
  });
  layer.draw();
  if (!restaurando) { atualizarValidade(); renderMinimap(); }
}

function findPortCircle(nodeId, portName, direction) {
  const group = canvasNodes[nodeId]?.group;
  if (!group) return null;
  return group.find('Circle').find(c => {
    const m = portMetaOf(c);
    return m && m.port_name === portName && m.direction === direction;
  }) || null;
}

function portAbsolutePos(nodeId, portName, direction) {
  const circle = findPortCircle(nodeId, portName, direction);
  return circle ? circle.getAbsolutePosition() : null;
}

function redrawEdges() {
  canvasEdges.forEach(e => {
    const p1 = portAbsolutePos(e.source_node, e.source_port, 'output');
    const p2 = portAbsolutePos(e.target_node, e.target_port, 'input');
    if (p1 && p2) e.line.points([p1.x, p1.y, p2.x, p2.y]);
  });
  layer.batchDraw();
}

function addEdge(edge) {
  const p1 = portAbsolutePos(edge.source_node, edge.source_port, 'output');
  const p2 = portAbsolutePos(edge.target_node, edge.target_port, 'input');
  if (!p1 || !p2) return;
  const line = new Konva.Line({
    points: [p1.x, p1.y, p2.x, p2.y], stroke: COLOR_EDGE, strokeWidth: 2, hitStrokeWidth: 12,
  });
  const entry = { ...edge, line };
  line.on('dblclick dbltap', () => {
    line.destroy();
    canvasEdges = canvasEdges.filter(x => x !== entry);
    layer.draw();
    pushHistory();
    atualizarValidade();
    renderMinimap();
  });
  line.moveToBottom();
  layer.add(line);
  canvasEdges.push(entry);
}

function desarmar() {
  if (armedPort) armedPort.circle.fill(COLOR_PORT);
  armedPort = null;
}

function onPortClick(evt) {
  const circle = evt.target;
  const meta = portMetaOf(circle);

  if (!armedPort) {
    armedPort = { ...meta, circle };
    circle.fill(COLOR_PORT_ARMED);
    layer.draw();
    return;
  }

  if (armedPort.node_id === meta.node_id && armedPort.port_name === meta.port_name) {
    desarmar();
    layer.draw();
    return;
  }

  let source = armedPort, target = meta;
  if (source.direction === 'input' && target.direction === 'output') { [source, target] = [target, source]; }
  if (source.direction !== 'output' || target.direction !== 'input') {
    showToast('Conecte uma porta de saída a uma porta de entrada', 'error');
    desarmar();
    layer.draw();
    return;
  }

  addEdge({
    source_node: source.node_id, source_port: source.port_name,
    target_node: target.node_id, target_port: target.port_name,
  });
  desarmar();
  layer.draw();
  pushHistory();
  atualizarValidade();
  renderMinimap();
}

function clearCanvas() {
  Object.values(canvasNodes).forEach(n => n.group.destroy());
  canvasEdges.forEach(e => e.line.destroy());
  canvasNodes = {};
  canvasEdges = [];
  nodeStatus = {};
  desarmar();
  layer.draw();
  renderMinimap();
}

function exportTopology() {
  return {
    nodes: Object.entries(canvasNodes).map(([node_id, n]) => {
      const node = { node_id, x: n.group.x(), y: n.group.y() };
      if (n.config && Object.keys(n.config).length) node.config = n.config;
      return node;
    }),
    edges: canvasEdges.map(({ source_node, source_port, target_node, target_port }) => ({
      source_node, source_port, target_node, target_port,
    })),
  };
}

function loadTopology(topologyJson) {
  clearCanvas();
  (topologyJson.nodes || []).forEach(n => {
    if (!nodesById[n.node_id]) return;  // componente não existe mais no registry — pula
    canvasNodes[n.node_id] = {
      group: buildNodeGroup(n.node_id, n.x || 40, n.y || 40),
      config: n.config || {},
    };
  });
  layer.draw();
  (topologyJson.edges || []).forEach(addEdge);
  layer.draw();
  renderMinimap();
}

const CAT_PALETTE = {
  trigger: 'Entrada / Saída', channel: 'Entrada / Saída', lab_router: 'Entrada / Saída',
  llm_provider: 'Modelos & IA', stt_provider: 'Modelos & IA', tts_provider: 'Modelos & IA', embeddings_provider: 'Modelos & IA',
  parser: 'Processamento', tool: 'Processamento',
};

// ── Painel de propriedades do nó (config_schema → formulário) ──────────────
let propsNoAtual = null;

function abrirPropsNo(nodeId) {
  const meta = nodesById[nodeId];
  const schema = meta?.config_schema;
  const props = schema && schema.properties;
  const painel = document.getElementById('node-props');
  if (!props || !Object.keys(props).length) { fecharPropsNo(); return; }

  propsNoAtual = nodeId;
  document.getElementById('node-props-title').textContent = nodeLabel(nodeId);
  const cfg = canvasNodes[nodeId]?.config || {};
  document.getElementById('node-props-body').innerHTML = Object.entries(props).map(([chave, spec]) => {
    const val = cfg[chave] ?? spec.default ?? '';
    const tipo = spec.type === 'number' ? 'number' : 'text';
    const step = spec.type === 'number' ? ' step="0.1"' : '';
    return `<div class="field">
      <label class="field__label" for="np-${chave}">${fmt.esc(spec.title || chave)}</label>
      <input class="input" id="np-${chave}" type="${tipo}"${step} value="${fmt.esc(val)}"
        data-key="${fmt.esc(chave)}" data-type="${spec.type || 'string'}">
      ${spec.description ? `<span class="field__hint">${fmt.esc(spec.description)}</span>` : ''}
    </div>`;
  }).join('') + '<button type="button" class="btn btn--primary btn--sm" id="np-apply">Aplicar</button>';

  document.getElementById('np-apply').onclick = () => {
    const novo = {};
    document.querySelectorAll('#node-props-body [data-key]').forEach((inp) => {
      const v = inp.value.trim();
      if (v === '') return;
      novo[inp.dataset.key] = inp.dataset.type === 'number' ? Number(v) : v;
    });
    if (canvasNodes[nodeId]) canvasNodes[nodeId].config = novo;
    pushHistory();
    showToast('Configuração aplicada');
    fecharPropsNo();
  };
  painel.hidden = false;
}

function fecharPropsNo() {
  propsNoAtual = null;
  document.getElementById('node-props').hidden = true;
}
document.getElementById('node-props-x').onclick = fecharPropsNo;

async function loadPalette() {
  const el = document.getElementById('palette');
  try {
    const r = await fetch('/hub/graph-studio/nodes', { credentials: 'same-origin' });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    nodesById = Object.fromEntries(d.nodes.map(n => [n.id, n]));

    const grupos = {};
    d.nodes.forEach(n => {
      const g = CAT_PALETTE[n.type] || 'Outros';
      (grupos[g] = grupos[g] || []).push(n);
    });

    el.innerHTML = '';
    Object.entries(grupos).forEach(([nome, nodes]) => {
      const lbl = document.createElement('div');
      lbl.className = 'studio-palette__group-label';
      lbl.textContent = nome;
      el.appendChild(lbl);
      nodes.forEach(n => {
        const btn = document.createElement('button');
        btn.className = 'studio-palette__item';
        btn.type = 'button';
        btn.textContent = Glossario.rotulo('node:' + n.id, n.metadata.name || n.id);
        btn.dataset.tech = n.id;
        btn.dataset.search = `${n.id} ${n.type} ${btn.textContent} ${n.metadata.description || ''}`.toLowerCase();
        btn.title = Glossario.ajuda('node:' + n.id) || n.metadata.description || '';
        btn.onclick = () => { addNodeToCanvas(n.id); atualizarValidade(); };
        el.appendChild(btn);
      });
    });
  } catch (e) {
    el.innerHTML = `<span class="badge badge--danger">${fmt.esc(e.message)}</span>`;
  }
}

document.getElementById('palette-search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('.studio-palette__item').forEach(b => {
    b.hidden = q && !(b.dataset.search || '').includes(q);
  });
});

function showErrors(msgs) {
  const el = document.getElementById('errors');
  if (!msgs || !msgs.length) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  el.innerHTML = `<strong>Topologia inválida:</strong><ul>${msgs.map(m => `<li>${fmt.esc(m)}</li>`).join('')}</ul>`;
}

// ── Lista lateral de fluxos salvos, com miniatura do DAG ───────────────────
const STATUS_BADGE = { testado: 'badge--ok', publicado: 'badge--ok' };
let topoCache = [];

function miniDag(topologyJson, w = 56, h = 34) {
  const nodes = (topologyJson.nodes || []).filter(n => Number.isFinite(n.x) && Number.isFinite(n.y));
  if (!nodes.length) return `<svg class="topo-card__dag" viewBox="0 0 ${w} ${h}"></svg>`;
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const minX = Math.min(...xs) - 20, minY = Math.min(...ys) - 20;
  const maxX = Math.max(...xs) + NODE_WIDTH + 20, maxY = Math.max(...ys) + 120;
  const s = Math.min((w - 4) / Math.max(maxX - minX, 1), (h - 4) / Math.max(maxY - minY, 1));
  const edges = (topologyJson.edges || []).map(e => {
    const a = nodes.find(n => n.node_id === e.source_node), b = nodes.find(n => n.node_id === e.target_node);
    if (!a || !b) return '';
    return `<line x1="${(a.x - minX) * s + NODE_WIDTH * s}" y1="${(a.y - minY) * s + 4}" x2="${(b.x - minX) * s}" y2="${(b.y - minY) * s + 4}" stroke="#d97a3f" stroke-width="0.7"/>`;
  }).join('');
  const rects = nodes.map(n =>
    `<rect x="${(n.x - minX) * s + 2}" y="${(n.y - minY) * s + 2}" width="${Math.max(6, NODE_WIDTH * s)}" height="6" rx="1.5" fill="#1b1f26" stroke="#333a44" stroke-width="0.5"/>`).join('');
  return `<svg class="topo-card__dag" viewBox="0 0 ${w} ${h}">${edges}${rects}</svg>`;
}

async function loadTopologiesList() {
  const box = document.getElementById('topo-list');
  try {
    const r = await fetch('/hub/graph-studio/topologies', { credentials: 'same-origin' });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    topoCache = d.topologies || [];
    const atual = document.getElementById('topo-name').value.trim();
    if (!topoCache.length) {
      box.innerHTML = '<span class="caption">Nenhum fluxo salvo. Arraste componentes e clique em Salvar.</span>';
      return;
    }
    box.innerHTML = topoCache.map(t => `
      <div class="topo-card" data-name="${fmt.esc(t.name)}" data-atual="${t.name === atual}">
        ${miniDag(t.topology_json || {})}
        <div class="topo-card__body">
          <div class="topo-card__name" title="${fmt.esc(t.name)}">${fmt.esc(t.name)}</div>
          <span class="badge ${STATUS_BADGE[t.status] || 'badge--neutral'}">${fmt.esc(t.status || 'rascunho')}</span>
        </div>
        <button class="btn btn--ghost btn--sm topo-card__del" data-del="${fmt.esc(t.name)}" aria-label="Remover">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
        </button>
      </div>`).join('');
    box.querySelectorAll('.topo-card').forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('[data-del]')) return;
        abrirTopologia(card.dataset.name);
      });
    });
    box.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', () => removerTopologia(b.dataset.del)));
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`;
  }
}

function abrirTopologia(name) {
  const topo = topoCache.find(t => t.name === name);
  if (!topo) return;
  document.getElementById('topo-name').value = topo.name;
  document.getElementById('topo-gatilho').value = topo.gatilho || '';
  document.getElementById('test-msg').value = topo.gatilho || '';
  fecharPropsNo();
  loadTopology(topo.topology_json || { nodes: [], edges: [] });
  showErrors([]);
  atualizarValidade();
  historico = []; historicoPos = -1; pushHistory();
  loadTopologiesList();
}

async function removerTopologia(name) {
  const ok = await confirmar({
    titulo: 'Remover fluxo',
    corpo: `Remover "${name}"? Esta ação não pode ser desfeita.`,
    acao: 'Remover', perigo: true,
  });
  if (!ok) return;
  try {
    const res = await fetch('/hub/graph-studio/remove', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      body: JSON.stringify({ name }),
    });
    const j = await res.json();
    if (j.error) throw new Error(j.error);
    showToast(`'${name}' removido`);
    if (document.getElementById('topo-name').value.trim() === name) {
      document.getElementById('topo-name').value = '';
      document.getElementById('topo-gatilho').value = '';
    }
    loadTopologiesList();
  } catch (e) { showToast(e.message, 'error'); }
}

async function salvarTopologia(status) {
  const name = document.getElementById('topo-name').value.trim();
  if (!name) { showToast('Dê um nome ao fluxo', 'error'); return null; }
  const body = {
    name, topology_json: exportTopology(),
    gatilho: document.getElementById('topo-gatilho').value.trim() || null,
  };
  if (status) body.status = status;
  const res = await fetch('/hub/graph-studio/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  return res.json();
}

document.getElementById('btn-save').addEventListener('click', async () => {
  try {
    const j = await salvarTopologia();
    if (!j) return;
    if (j.error) { showErrors(j.detalhes || [j.error]); showToast(j.error, 'error'); return; }
    showErrors([]);
    showToast(`Fluxo '${j.name}' salvo (v${j.versao})`);
    loadTopologiesList();
  } catch (e) { showToast(e.message, 'error'); }
});

document.getElementById('btn-new').addEventListener('click', () => {
  clearCanvas();
  document.getElementById('topo-name').value = '';
  document.getElementById('topo-gatilho').value = '';
  fecharPropsNo();
  showErrors([]);
  atualizarValidade();
  pushHistory();
  loadTopologiesList();
});

document.getElementById('btn-dup').addEventListener('click', async () => {
  const name = document.getElementById('topo-name').value.trim();
  if (!name) { showToast('Abra ou salve um fluxo antes de duplicar', 'error'); return; }
  const novo = `${name} (cópia)`;
  document.getElementById('topo-name').value = novo;
  try {
    const j = await salvarTopologia();
    if (j && j.error) { showToast(j.error, 'error'); document.getElementById('topo-name').value = name; return; }
    showToast(`Duplicado como '${novo}'`);
    loadTopologiesList();
  } catch (e) { showToast(e.message, 'error'); }
});

// ── Toolbar do canvas ─────────────────────────────────────────────────────
document.getElementById('zoom-in').onclick = () => zoom(1.15);
document.getElementById('zoom-out').onclick = () => zoom(0.87);
document.getElementById('zoom-fit').onclick = zoomFit;
document.getElementById('btn-undo').onclick = () => restaurar(historicoPos - 1);
document.getElementById('btn-redo').onclick = () => restaurar(historicoPos + 1);
document.getElementById('canvas-clear').onclick = async () => {
  if (!Object.keys(canvasNodes).length) return;
  if (!await confirmar({ titulo: 'Limpar área', corpo: 'Remove todos os componentes e ligações do desenho atual.', acao: 'Limpar', perigo: true })) return;
  clearCanvas();
  atualizarValidade();
  pushHistory();
};
document.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); restaurar(historicoPos - 1); }
  else if ((e.key === 'y') || (e.key === 'z' && e.shiftKey)) { e.preventDefault(); restaurar(historicoPos + 1); }
});

// ── Exportar / importar ───────────────────────────────────────────────────
document.getElementById('btn-export').onclick = () => {
  const nome = document.getElementById('topo-name').value.trim() || 'desenho';
  const blob = new Blob([JSON.stringify(exportTopology(), null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${nome}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
};
// ── Ver caminho (dry-run — destaca a ordem, não chama componente) ──────────
document.getElementById('btn-test').addEventListener('click', async () => {
  const name = document.getElementById('topo-name').value.trim();
  if (!name) { showToast('Salve o desenho antes de testar', 'error'); return; }
  showErrors([]);
  try {
    const res = await fetch('/hub/graph-studio/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      body: JSON.stringify({ name, modo: 'caminho' }),
    });
    const j = await res.json();
    if (j.error) { showToast(j.error, 'error'); return; }
    if (j.erros && j.erros.length) { showErrors(j.erros); showToast('Topologia inválida', 'error'); return; }
    await destacarCaminho(j.ordem || [], j.eventos || []);
  } catch (e) { showToast(e.message, 'error'); }
});

// ── Rodar teste real (sandbox — executa os componentes de verdade) ─────────
const STEP_ICON = {
  running: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.2-8.5"/></svg>',
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  skip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/></svg>',
};
const CANVAS_COR = { running: '#c9982e', ok: '#3ba55c', error: '#e05d44', skip: '#7b8394' };
let ultimoTestePassou = false;

function pintarStatusNos() {
  Object.entries(canvasNodes).forEach(([id, { group }]) => {
    const rect = group.findOne('Rect');
    const st = nodeStatus[id];
    if (st) { rect.stroke(CANVAS_COR[st] || '#262b33'); rect.strokeWidth(st === 'ok' ? 2.5 : 2); }
    else { rect.stroke('#262b33'); rect.strokeWidth(1); }
  });
  layer.batchDraw();
}

function resumoEventosPorNo(eventos) {
  const porNo = {};
  for (const e of eventos) {
    if (!e.node || e.node === '-') continue;
    const cur = porNo[e.node] || (porNo[e.node] = {});
    if (e.tipo === 'iniciando') cur.status = cur.status || 'running';
    if (e.tipo === 'concluido') { cur.status = 'ok'; cur.ms = e.ms; cur.tokens = e.tokens; }
    if (e.tipo === 'pulado') cur.status = 'skip';
    if (e.tipo === 'erro') { cur.status = 'error'; cur.erro = e.erro; }
  }
  return porNo;
}

function renderSteps(ordem, porNo) {
  const box = document.getElementById('test-steps');
  box.innerHTML = ordem.map((id) => {
    const s = porNo[id] || { status: 'skip' };
    const meta = [
      s.ms != null ? `${s.ms} ms` : '',
      s.tokens ? `${s.tokens[0] + s.tokens[1]} tokens` : '',
      s.erro ? s.erro : '',
    ].filter(Boolean).join(' · ');
    return `<div class="studio-step ${s.status}">
      <span class="studio-step__icon">${STEP_ICON[s.status] || STEP_ICON.running}</span>
      <div class="studio-step__body">
        <div class="studio-step__name">${fmt.esc(nodeLabel(id))}</div>
        ${meta ? `<div class="studio-step__meta">${fmt.esc(meta)}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

document.getElementById('btn-run-test').addEventListener('click', async () => {
  const name = document.getElementById('topo-name').value.trim();
  if (!name) { showToast('Salve o fluxo antes de testar', 'error'); return; }
  const msg = document.getElementById('test-msg').value.trim();
  if (!msg) { showToast('Escreva uma mensagem de teste', 'error'); return; }

  const btn = document.getElementById('btn-run-test');
  const verdictEl = document.getElementById('test-verdict');
  const answerEl = document.getElementById('test-answer');
  const markBtn = document.getElementById('btn-mark-tested');
  btn.disabled = true;
  verdictEl.hidden = true; answerEl.hidden = true; markBtn.hidden = true;
  document.getElementById('test-steps').innerHTML = '<span class="caption">Rodando…</span>';
  showErrors([]);

  try {
    const res = await fetch('/hub/graph-studio/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      body: JSON.stringify({ name, modo: 'sandbox', mensagem_teste: msg }),
    });
    const j = await res.json();
    if (j.error) { document.getElementById('test-steps').innerHTML = ''; showToast(j.error, 'error'); return; }

    if (j.erros && j.erros.length) {
      showErrors(j.erros);
      document.getElementById('test-steps').innerHTML = '';
    }

    const ordem = j.ordem || [];
    const porNo = resumoEventosPorNo(j.eventos || []);
    renderSteps(ordem, porNo);
    nodeStatus = Object.fromEntries(Object.entries(porNo).map(([id, s]) => [id, s.status || 'ok']));
    pintarStatusNos();

    const passou = j.ok && !(j.erros || []).length;
    ultimoTestePassou = passou;
    verdictEl.hidden = false;
    verdictEl.className = `studio-test__verdict ${passou ? 'studio-test__verdict--ok' : 'studio-test__verdict--err'}`;
    verdictEl.textContent = passou ? `Passou · ${j.duracao_ms} ms` : 'Falhou';

    if (j.resposta) {
      answerEl.hidden = false;
      answerEl.textContent = j.resposta;
    }
    markBtn.hidden = !passou;
  } catch (e) {
    document.getElementById('test-steps').innerHTML = '';
    showToast(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('btn-mark-tested').addEventListener('click', async () => {
  if (!ultimoTestePassou) return;
  try {
    const j = await salvarTopologia('testado');
    if (!j) return;
    if (j.error) { showToast(j.error, 'error'); return; }
    showToast('Fluxo marcado como testado');
    document.getElementById('btn-mark-tested').hidden = true;
    loadTopologiesList();
  } catch (e) { showToast(e.message, 'error'); }
});

async function destacarCaminho(ordem, eventos) {
  const pulados = new Set(eventos.filter(e => e.tipo === 'pulado').map(e => e.node));
  for (const nodeId of ordem) {
    const g = canvasNodes[nodeId]?.group;
    if (!g) continue;
    const rect = g.findOne('Rect');
    rect.stroke(pulados.has(nodeId) ? '#7b8394' : '#3ba55c');
    rect.strokeWidth(pulados.has(nodeId) ? 1 : 2.5);
    layer.batchDraw();
    await new Promise(r => setTimeout(r, 320));
  }
  const n = ordem.length - pulados.size;
  showToast(`Caminho: ${n} componente(s)${pulados.size ? `, ${pulados.size} pulado(s)` : ''}`);
  setTimeout(() => {
    Object.values(canvasNodes).forEach(({ group }) => {
      const rect = group.findOne('Rect');
      rect.stroke('#262b33'); rect.strokeWidth(1);
    });
    layer.batchDraw();
  }, 2500);
}

document.getElementById('btn-import').onclick = () => document.getElementById('import-file').click();
document.getElementById('import-file').onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      loadTopology(JSON.parse(reader.result));
      atualizarValidade();
      showToast('Desenho importado');
    } catch { showToast('Arquivo inválido', 'error'); }
  };
  reader.readAsText(file);
  e.target.value = '';
};

// ── Aba "Fluxos de produção" (somente leitura) ────────────────────────────
const REF_NODE_W = 150, REF_NODE_H = 34;
let refCarregado = false;

function svgFluxo(f) {
  const maxX = Math.max(...f.nodes.map(n => n.x)) + REF_NODE_W + 20;
  const maxY = Math.max(...f.nodes.map(n => n.y)) + REF_NODE_H + 20;
  const pos = Object.fromEntries(f.nodes.map(n => [n.id, n]));
  const arestas = f.edges.map(e => {
    const a = pos[e.de], b = pos[e.para];
    const x1 = a.x + REF_NODE_W, y1 = a.y + REF_NODE_H / 2;
    const x2 = b.x, y2 = b.y + REF_NODE_H / 2;
    const mx = (x1 + x2) / 2;
    const label = e.rotulo
      ? `<text x="${mx}" y="${(y1 + y2) / 2 - 4}" fill="#7b8394" font-size="9" text-anchor="middle">${fmt.esc(e.rotulo)}</text>` : '';
    return `<path d="M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" fill="none" stroke="#d97a3f" stroke-width="1.4" marker-end="url(#ref-arrow)"/>${label}`;
  }).join('');
  const caixas = f.nodes.map(n => `
    <g transform="translate(${n.x} ${n.y})">
      <rect width="${REF_NODE_W}" height="${REF_NODE_H}" rx="6" fill="#12151a" stroke="#262b33"/>
      <text x="${REF_NODE_W / 2}" y="${REF_NODE_H / 2 + 3}" fill="#e8eaed" font-size="10" text-anchor="middle">${fmt.esc(n.label)}</text>
    </g>`).join('');
  return `<div class="ref-flow">
    <div class="ref-flow__head">
      <strong>${fmt.esc(f.nome)}</strong>
      <span class="caption" data-tech="${fmt.esc(f.fonte)}">${fmt.esc(f.descricao)}</span>
    </div>
    <div class="ref-flow__canvas">
      <svg viewBox="0 0 ${maxX} ${maxY}" width="${maxX}" height="${maxY}">
        <defs><marker id="ref-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#d97a3f"/></marker></defs>
        ${arestas}${caixas}
      </svg>
    </div>
  </div>`;
}

async function carregarReferencia() {
  if (refCarregado) return;
  const box = document.getElementById('ref-flows');
  try {
    const r = await fetch('/hub/graph-studio/reference', { credentials: 'same-origin' });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    box.innerHTML = (d.fluxos || []).map(svgFluxo).join('');
    refCarregado = true;
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`;
  }
}

window.studioPane = function (pane) {
  document.getElementById('pane-editor').hidden = pane !== 'editor';
  document.getElementById('pane-ref').hidden = pane !== 'ref';
  if (pane === 'ref') carregarReferencia();
};

(async function init() {
  initStage();
  await loadPalette();
  await loadTopologiesList();
  atualizarValidade();
  renderMinimap();
  pushHistory();
})();
