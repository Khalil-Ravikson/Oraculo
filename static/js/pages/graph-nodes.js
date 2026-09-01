/* graph-nodes.js — catálogo de componentes (Hub v2 Sprint 4).
   Cards por categoria, StatusPill de saúde, portas como PortTag,
   Switch no cabeçalho. Filtro por aba (Alpine cuida do aria-selected/hash;
   este JS cuida de mostrar/esconder os cards). */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { hub } from '/static/js/core/api-client.js';
import { Glossario } from '/static/js/core/glossario.js';

const box = document.getElementById('nodes');

const CATEGORIA = {
  trigger: 'canais', channel: 'canais',
  llm_provider: 'providers', stt_provider: 'providers', tts_provider: 'providers', embeddings_provider: 'providers',
  parser: 'parsers',
  tool: 'mcp', lab_router: 'mcp',
};
const CAT_LABEL = {
  trigger: 'Mensagem de teste', channel: 'Canal', llm_provider: 'Modelo de linguagem', stt_provider: 'Áudio → texto',
  tts_provider: 'Texto → áudio', embeddings_provider: 'Vetor semântico', parser: 'Leitor de documento',
  tool: 'Ferramenta', lab_router: 'Laboratório',
};

function statusPill(health) {
  if (!health) return '<span class="badge badge--unknown status-pill">Não monitorado</span>';
  return health.is_healthy
    ? `<span class="badge badge--ok status-pill" title="${fmt.esc(health.detail || '')}">Operacional</span>`
    : `<span class="badge badge--danger status-pill" title="${fmt.esc(health.error || '')}">Com erro</span>`;
}

function portTags(ports, dir) {
  if (!ports || !ports.length) return '<span class="caption">—</span>';
  return ports.map((p) => {
    const opt = p.required === false;
    return `<span class="port-tag port-tag--${dir}" data-type="${fmt.esc(p.type)}"${opt ? ' data-opt="true"' : ''} title="${fmt.esc(p.description || '')}">${fmt.esc(Glossario.rotulo('port:' + p.type, p.type))}${opt ? ' · opcional' : ''}</span>`;
  }).join('');
}

function nodeCard(n) {
  const cat = CATEGORIA[n.type] || 'todos';
  const nome = Glossario.rotulo('node:' + n.id, n.metadata?.name || n.id);
  const desc = Glossario.ajuda('node:' + n.id) || '';
  return `<div class="card card--resource" data-cat="${cat}" data-id="${fmt.esc(n.id)}">
    <div class="card__head">
      <span class="card__title" data-tech="${fmt.esc(n.id)}">${fmt.esc(nome)}</span>
      <label class="toggle" title="${n.habilitado ? 'Desativar' : 'Ativar'}">
        <input type="checkbox" ${n.habilitado ? 'checked' : ''} data-toggle="${fmt.esc(n.id)}">
        <span class="toggle__track"></span>
      </label>
    </div>
    <div class="u-flex u-gap-2 u-items-center u-wrap">
      <span class="badge badge--neutral">${fmt.esc(CAT_LABEL[n.type] || n.type)}</span>
      ${statusPill(n.health)}
    </div>
    <div class="card__desc">${fmt.esc(desc)}</div>
    <div class="ports">
      <div class="ports__group"><span class="ports__label">entrada</span>${portTags(n.input_ports, 'in')}</div>
      <div class="ports__group"><span class="ports__label">saída</span>${portTags(n.output_ports, 'out')}</div>
    </div>
  </div>`;
}

function aplicarFiltro() {
  const ativa = document.querySelector('.tab[aria-selected="true"]')?.dataset.tab || 'todos';
  box.querySelectorAll('[data-cat]').forEach((el) => {
    el.classList.toggle('u-hidden', ativa !== 'todos' && el.dataset.cat !== ativa);
  });
}

async function load() {
  try {
    const d = await hub.get('/graph-nodes/data');
    box.innerHTML = d.nodes.map(nodeCard).join('') || '<span class="caption">Nenhum componente registrado.</span>';
    box.querySelectorAll('[data-toggle]').forEach((el) => el.onchange = async () => {
      try {
        const j = await hub.post('/graph-nodes/toggle', { node_id: el.dataset.toggle, habilitado: el.checked });
        showToast(`${j.node_id}: ${j.habilitado ? 'ativado' : 'desativado'}`);
      } catch (e) { el.checked = !el.checked; showToast(e.message, 'error'); }
    });
    aplicarFiltro();
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">Erro: ${fmt.esc(e.message)}</span>`;
  }
}

document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => setTimeout(aplicarFiltro, 0)));
window.addEventListener('hashchange', aplicarFiltro);

load();
