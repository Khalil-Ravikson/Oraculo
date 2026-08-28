/* graph-studio.js — canvas de composição visual (Camada 3, Konva.js vendorado).
   Interação: clique num nó da paleta pra adicionar; clique numa porta de saída
   e depois numa porta de entrada compatível pra conectar; arrastar reposiciona;
   duplo-clique num nó ou aresta remove. Validação real (tipos + DAG) roda no
   servidor ao salvar — o client só bloqueia output->output/input->input. */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';

const NODE_WIDTH = 180;
const NODE_HEADER = 28;
const PORT_ROW_H = 20;
const COLOR_PORT = '#7b8394';
const COLOR_PORT_ARMED = '#d97a3f';
const COLOR_EDGE = '#d97a3f';

let stage, layer;
let nodesById = {};      // node_id -> metadata do registry (portas, tipos)
let canvasNodes = {};    // node_id -> { group }
let canvasEdges = [];    // [{ source_node, source_port, target_node, target_port, line }]
let armedPort = null;    // { node_id, port_name, direction, circle }

function initStage() {
  const container = document.getElementById('canvas');
  stage = new Konva.Stage({ container: 'canvas', width: container.clientWidth || 900, height: 640 });
  layer = new Konva.Layer();
  stage.add(layer);
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
    text: nodeId, x: 8, y: 8, fontSize: 12, fontFamily: 'monospace', fill: '#e8eaed',
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
  group.on('dblclick dbltap', (evt) => {
    // só remove se o alvo do duplo-clique for o retângulo/fundo, não uma porta
    if (evt.target.getAttr('portMeta')) return;
    removeNode(nodeId);
  });

  layer.add(group);
  return group;
}

function addNodeToCanvas(nodeId) {
  if (canvasNodes[nodeId]) { showToast(`'${nodeId}' já está no canvas`, 'error'); return; }
  const n = Object.keys(canvasNodes).length;
  const x = 40 + (n % 3) * 220;
  const y = 40 + Math.floor(n / 3) * 160;
  canvasNodes[nodeId] = { group: buildNodeGroup(nodeId, x, y) };
  layer.draw();
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
}

function clearCanvas() {
  Object.values(canvasNodes).forEach(n => n.group.destroy());
  canvasEdges.forEach(e => e.line.destroy());
  canvasNodes = {};
  canvasEdges = [];
  desarmar();
  layer.draw();
}

function exportTopology() {
  return {
    nodes: Object.entries(canvasNodes).map(([node_id, n]) => ({
      node_id, x: n.group.x(), y: n.group.y(),
    })),
    edges: canvasEdges.map(({ source_node, source_port, target_node, target_port }) => ({
      source_node, source_port, target_node, target_port,
    })),
  };
}

function loadTopology(topologyJson) {
  clearCanvas();
  (topologyJson.nodes || []).forEach(n => {
    if (!nodesById[n.node_id]) return;  // nó não existe mais no registry — pula
    canvasNodes[n.node_id] = { group: buildNodeGroup(n.node_id, n.x || 40, n.y || 40) };
  });
  layer.draw();
  (topologyJson.edges || []).forEach(addEdge);
  layer.draw();
}

async function loadPalette() {
  const el = document.getElementById('palette');
  try {
    const r = await fetch('/hub/graph-studio/nodes');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    nodesById = Object.fromEntries(d.nodes.map(n => [n.id, n]));
    el.innerHTML = '';
    d.nodes.forEach(n => {
      const btn = document.createElement('button');
      btn.className = 'studio-palette__item';
      btn.type = 'button';
      btn.textContent = n.id;
      btn.title = n.metadata.description || '';
      btn.onclick = () => addNodeToCanvas(n.id);
      el.appendChild(btn);
    });
  } catch (e) {
    el.innerHTML = `<span class="badge badge--danger">${fmt.esc(e.message)}</span>`;
  }
}

async function loadTopologiesDropdown() {
  const sel = document.getElementById('topo-load');
  try {
    const r = await fetch('/hub/graph-studio/topologies');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    sel.innerHTML = '<option value="">Carregar topologia salva…</option>' +
      d.topologies.map(t => `<option value="${fmt.esc(t.name)}">${fmt.esc(t.name)}</option>`).join('');
    sel.dataset.cache = JSON.stringify(d.topologies);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function showErrors(msgs) {
  const el = document.getElementById('errors');
  if (!msgs || !msgs.length) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  el.innerHTML = `<strong>Topologia inválida:</strong><ul>${msgs.map(m => `<li>${fmt.esc(m)}</li>`).join('')}</ul>`;
}

document.getElementById('btn-save').addEventListener('click', async () => {
  const name = document.getElementById('topo-name').value.trim();
  if (!name) { showToast('Dê um nome pra topologia', 'error'); return; }
  try {
    const res = await fetch('/hub/graph-studio/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, topology_json: exportTopology() }),
    });
    const j = await res.json();
    if (j.error) {
      showErrors(j.detalhes || [j.error]);
      showToast(j.error, 'error');
      return;
    }
    showErrors([]);
    showToast(`Topologia '${j.name}' salva (v${j.versao})`);
    loadTopologiesDropdown();
  } catch (e) { showToast(e.message, 'error'); }
});

document.getElementById('btn-new').addEventListener('click', () => {
  clearCanvas();
  document.getElementById('topo-name').value = '';
  showErrors([]);
});

document.getElementById('btn-delete').addEventListener('click', async () => {
  const name = document.getElementById('topo-load').value;
  if (!name) { showToast('Selecione uma topologia salva pra remover', 'error'); return; }
  const ok = await confirmar({
    titulo: 'Remover topologia',
    corpo: `Remover "${name}"? Esta ação não pode ser desfeita.`,
    acao: 'Remover', perigo: true,
  });
  if (!ok) return;
  try {
    const res = await fetch('/hub/graph-studio/remove', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const j = await res.json();
    if (j.error) throw new Error(j.error);
    showToast(`'${name}' removida`);
    loadTopologiesDropdown();
  } catch (e) { showToast(e.message, 'error'); }
});

document.getElementById('topo-load').addEventListener('change', (ev) => {
  const name = ev.target.value;
  if (!name) return;
  const cache = JSON.parse(document.getElementById('topo-load').dataset.cache || '[]');
  const topo = cache.find(t => t.name === name);
  if (!topo) return;
  document.getElementById('topo-name').value = topo.name;
  loadTopology(topo.topology_json);
  showErrors([]);
});

(async function init() {
  initStage();
  await loadPalette();
  await loadTopologiesDropdown();
})();
