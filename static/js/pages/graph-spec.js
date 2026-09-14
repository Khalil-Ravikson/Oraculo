/* graph-spec.js — aba "Grafo de produção" do Graph Studio.
   Mostra a GraphSpec ativa (topologia real, versionada) e deixa criar/remover
   fluxos terminais. O esqueleto (classificação + funis) é travado no back-end;
   aqui a GUI só oferece o que dá pra editar com segurança. */
import { fmt } from '/static/js/core/format.js';
import { hub } from '/static/js/core/api-client.js';
import { showToast } from '/static/js/core/toast.js';
import { formModal } from '/static/js/core/modal.js';

let estado = null;   // resposta do GET /graph-studio/spec
let mermaidPronto = false;

const $ = (id) => document.getElementById(id);

function garantirMermaid() {
  if (mermaidPronto || !window.mermaid) return;
  window.mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'strict',
    flowchart: { curve: 'basis', htmlLabels: false },
  });
  mermaidPronto = true;
}

async function renderDiagrama() {
  const box = $('spec-diagram');
  const fluxo = (estado.diagrama && estado.diagrama.fluxos && estado.diagrama.fluxos[0]) || null;
  if (!estado.mermaid || !window.mermaid) {
    box.innerHTML = '<span class="caption">Diagrama indisponível.</span>';
    return;
  }
  garantirMermaid();
  try {
    const { svg } = await window.mermaid.render('gs-mermaid', estado.mermaid);
    const cabecalho = fluxo
      ? `<div class="ref-flow__head"><strong>${fmt.esc(fluxo.nome)}</strong>
          <span class="caption" data-tech="${fmt.esc(fluxo.fonte || '')}">${fmt.esc(fluxo.descricao || '')}</span></div>`
      : '';
    box.innerHTML = `<div class="ref-flow">${cabecalho}<div class="ref-flow__canvas">${svg}</div></div>`;
  } catch (e) {
    box.innerHTML = `<span style="color:var(--danger)">Falha ao desenhar o grafo: ${fmt.esc(e.message || String(e))}</span>`;
  }
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
      <button class="btn btn--ghost btn--sm" data-remove="${fmt.esc(r.node_id)}" disabled title="Edição do grafo desativada temporariamente">Remover</button>
    </div>`).join('');
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
  await renderDiagrama();
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
      <button type="button" class="btn btn--ghost btn--sm" disabled title="Edição do grafo desativada temporariamente">Reverter para esta</button>
    </div>`).join('');
  await formModal({
    titulo: 'Histórico da topologia',
    corpo: `<div class="spec-hist">${linhas}</div>`,
    acao: 'Fechar',
    onSubmit: () => true,
  });
}

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
