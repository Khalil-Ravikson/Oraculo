/* graph-spec.js — aba "Grafo de produção" do Graph Studio.
   Mostra a GraphSpec ativa (topologia real, versionada) e deixa criar/remover
   fluxos terminais. O esqueleto (classificação + funis) é travado no back-end;
   aqui a GUI só oferece o que dá pra editar com segurança. */
import { fmt } from '/static/js/core/format.js';
import { hub } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { confirmar, formModal } from '/static/js/core/modal.js';

const REF_W = 150, REF_H = 34;
let estado = null;   // resposta do GET /graph-studio/spec

const $ = (id) => document.getElementById(id);

function svgFluxo(f) {
  if (!f.nodes || !f.nodes.length) return '';
  const maxX = Math.max(...f.nodes.map(n => n.x)) + REF_W + 24;
  const maxY = Math.max(...f.nodes.map(n => n.y)) + REF_H + 24;
  const pos = Object.fromEntries(f.nodes.map(n => [n.id, n]));
  const arestas = f.edges.map(e => {
    const a = pos[e.de], b = pos[e.para];
    if (!a || !b) return '';
    const x1 = a.x + REF_W, y1 = a.y + REF_H / 2, x2 = b.x, y2 = b.y + REF_H / 2;
    const mx = (x1 + x2) / 2;
    const label = e.rotulo
      ? `<text x="${mx}" y="${(y1 + y2) / 2 - 4}" fill="#7b8394" font-size="9" text-anchor="middle">${fmt.esc(e.rotulo)}</text>` : '';
    return `<path d="M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}" fill="none" stroke="#d97a3f" stroke-width="1.4" marker-end="url(#gs-arrow)"/>${label}`;
  }).join('');
  const caixas = f.nodes.map(n => `
    <g transform="translate(${n.x} ${n.y})">
      <rect width="${REF_W}" height="${REF_H}" rx="6" fill="#12151a" stroke="#262b33"/>
      <text x="${REF_W / 2}" y="${REF_H / 2 + 3}" fill="#e8eaed" font-size="10" text-anchor="middle">${fmt.esc(n.label)}</text>
    </g>`).join('');
  return `<div class="ref-flow"><div class="ref-flow__head">
      <strong>${fmt.esc(f.nome)}</strong>
      <span class="caption" data-tech="${fmt.esc(f.fonte || '')}">${fmt.esc(f.descricao || '')}</span>
    </div><div class="ref-flow__canvas">
      <svg viewBox="0 0 ${maxX} ${maxY}" width="${maxX}" height="${maxY}">
        <defs><marker id="gs-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#d97a3f"/></marker></defs>
        ${arestas}${caixas}
      </svg></div></div>`;
}

function renderDiagrama() {
  const box = $('spec-diagram');
  const fluxos = (estado.diagrama && estado.diagrama.fluxos) || [];
  box.innerHTML = fluxos.map(svgFluxo).join('') || '<span class="caption">Diagrama indisponível.</span>';
}

function renderVersao() {
  $('spec-versao').textContent = estado.versao || '—';
  $('spec-por').textContent = estado.atualizado_por ? `· por ${estado.atualizado_por}` : '';
}

function renderCustom() {
  const box = $('spec-custom-list');
  const rotas = estado.rotas_editaveis || [];
  if (!rotas.length) {
    box.innerHTML = '<span class="caption">Nenhum fluxo personalizado ainda.</span>';
    return;
  }
  box.innerHTML = rotas.map(r => `
    <div class="spec-custom-row">
      <div>
        <strong>${fmt.esc(r.node_id)}</strong>
        <span class="badge badge--neutral">${fmt.esc(r.node_type)}</span>
        ${r.config && r.config.doc_type ? `<span class="caption">doc: ${fmt.esc(r.config.doc_type)} · k ${fmt.esc(String(r.config.k ?? ''))}</span>` : ''}
      </div>
      <button class="btn btn--ghost btn--sm" data-remove="${fmt.esc(r.node_id)}">Remover</button>
    </div>`).join('');
  box.querySelectorAll('[data-remove]').forEach(b => {
    b.onclick = () => removerRota(b.dataset.remove);
  });
}

function renderNodeTypes() {
  const sel = $('spec-node-type');
  const tipos = estado.tipos_adicionaveis || ['rag'];
  const rotulo = Object.fromEntries((estado.tipos || []).map(t => [t.nome, t.display_name]));
  sel.innerHTML = tipos.map(t => `<option value="${fmt.esc(t)}">${fmt.esc(rotulo[t] || t)}</option>`).join('');
  sel.onchange = toggleRagFields;
  toggleRagFields();
}

function toggleRagFields() {
  $('spec-rag-fields').style.display = $('spec-node-type').value === 'rag' ? '' : 'none';
}

async function carregar() {
  const box = $('spec-diagram');
  try {
    estado = await hub.get('/graph-studio/spec');
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message || 'Falha ao carregar')}</span>`;
    return;
  }
  renderVersao();
  renderDiagrama();
  renderCustom();
  renderNodeTypes();
}

async function criarRota(ev) {
  ev.preventDefault();
  const f = ev.target;
  const erros = $('spec-nova-erros');
  const msg = $('spec-nova-msg');
  erros.hidden = true; msg.textContent = 'Criando…';
  const body = {
    rota: f.rota.value.trim(),
    node_type: f.node_type.value,
    gatilho: f.gatilho.value.trim(),
    doc_type: f.doc_type.value.trim() || 'geral',
    k: parseInt(f.k.value, 10) || 6,
    cacheavel: f.cacheavel.checked,
    versao_esperada: estado ? estado.versao : 0,
  };
  try {
    const r = await hub.post('/graph-studio/spec/nova-rota', body);
    msg.textContent = '';
    showToast(`Fluxo "${r.rota}" criado (v${r.versao}). ${r.aviso || ''}`, 'success');
    f.reset();
    await carregar();
  } catch (e) {
    msg.textContent = '';
    const p = e.body || {};
    if (p.detalhes && p.detalhes.length) {
      erros.hidden = false;
      erros.innerHTML = '<strong>Não foi gravado:</strong><ul>' +
        p.detalhes.map(d => `<li>${fmt.esc(d)}</li>`).join('') + '</ul>';
    } else {
      showToast(p.error || e.message || 'Falha ao criar', 'error');
    }
  }
}

async function removerRota(nodeId) {
  const ok = await confirmar({
    titulo: 'Remover fluxo',
    corpo: `Remove o nó "${nodeId}", suas ligações no grafo e a rota correspondente. ` +
           `Entra em vigor no próximo restart dos workers.`,
    acao: 'Remover',
  });
  if (!ok) return;
  try {
    const r = await hub.post('/graph-studio/spec/rota/remover', { node_id: nodeId });
    showToast(`Fluxo removido (v${r.versao}).`, 'success');
    await carregar();
  } catch (e) {
    showToast((e.body && e.body.error) || e.message || 'Falha ao remover', 'error');
  }
}

async function abrirHistorico() {
  let hist = [];
  try {
    const d = await hub.get('/graph-studio/spec/historico');
    hist = d.historico || [];
  } catch (e) {
    showToast('Falha ao carregar histórico', 'error');
    return;
  }
  if (!hist.length) {
    showToast('Sem histórico ainda — nenhuma edição gravada.', 'info');
    return;
  }
  const linhas = hist.map(h => `
    <div class="spec-hist-row">
      <span>v${h.versao} · ${new Date(h.atualizado_em).toLocaleString('pt-BR')}
        ${h.atualizado_por ? `· ${fmt.esc(h.atualizado_por)}` : ''}</span>
      <button type="button" class="btn btn--ghost btn--sm" data-rev="${h.versao}">Reverter para esta</button>
    </div>`).join('');
  await formModal({
    titulo: 'Histórico da topologia',
    corpo: `<div class="spec-hist">${linhas}</div>`,
    acao: 'Fechar',
    onSubmit: () => true,
  });
}

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-rev]');
  if (!b) return;
  const versao = parseInt(b.dataset.rev, 10);
  const ok = await confirmar({
    titulo: `Reverter para v${versao}`,
    corpo: 'A topologia atual vira uma versão nova, com o conteúdo da v' + versao + '. Reversível.',
    acao: 'Reverter',
  });
  if (!ok) return;
  try {
    const r = await hub.post('/graph-studio/spec/reverter', { versao });
    showToast(`Revertido (v${r.versao}).`, 'success');
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
    await carregar();
  } catch (e) {
    showToast((e.body && e.body.error) || e.message || 'Falha ao reverter', 'error');
  }
});

let armado = false;
window.graphSpecPane = function () {
  if (!armado) {
    armado = true;
    $('spec-nova-rota').addEventListener('submit', criarRota);
    $('spec-hist-btn').addEventListener('click', abrirHistorico);
  }
  carregar();
};

// A aba "Grafo de produção" é a inicial — carrega ao abrir a página.
window.graphSpecPane();
