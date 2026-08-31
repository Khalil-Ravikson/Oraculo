/* infra-search.js — Busca & Índices (Hub v2 Sprint 6a). */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { hub } from '/static/js/core/api-client.js';

const $ = (id) => document.getElementById(id);

function campoBadge(c) {
  const cor = { texto: 'badge--neutral', etiqueta: 'badge--active', 'número': 'badge--neutral', vetor: 'badge--ok' }[c.tipo] || 'badge--neutral';
  return `<span class="badge ${cor}" style="margin:2px">${fmt.esc(c.nome)} <span class="caption">${fmt.esc(c.tipo)}</span></span>`;
}

function indiceCard(ix) {
  const v = ix.vetor;
  return `<div class="card card--resource" data-tech="${fmt.esc(ix.nome)}">
    <div class="card__head">
      <span class="card__title">${fmt.esc(ix.rotulo)}</span>
      ${ix.indexando
        ? `<span class="badge badge--warn status-pill">indexando ${ix.percent_indexado}%</span>`
        : (ix.falhas_indexacao ? `<span class="badge badge--danger status-pill">${ix.falhas_indexacao} falha(s)</span>` : '<span class="badge badge--ok status-pill">Pronto</span>')}
    </div>
    <dl class="res__meta">
      <dt>documentos</dt><dd>${fmt.num(ix.num_docs)}</dd>
      <dt>termos indexados</dt><dd>${fmt.num(ix.num_termos)}</dd>
      <dt>tamanho (texto / vetor)</dt><dd>${ix.tamanho_texto_mb} / ${ix.tamanho_vetor_mb} MB</dd>
    </dl>
    ${v ? `<dl class="kv u-mt-2">
      <dt>Modelo vetorial</dt><dd>${fmt.esc(v.algoritmo)} · ${v.dim} dim · ${fmt.esc(v.metrica)}</dd>
      <dt>Conectividade (M)</dt><dd>${v.M || '—'}</dd>
      <dt>Qualidade da indexação</dt><dd>${v.ef_construction || '—'}</dd>
      ${v.ef_runtime ? `<dt>Precisão da busca</dt><dd>${v.ef_runtime}</dd>` : ''}
    </dl>` : ''}
    <div class="u-flex u-wrap u-mt-2">${(ix.campos || []).map(campoBadge).join('')}</div>
  </div>`;
}

async function load() {
  try {
    const d = await hub.get('/infra/search/data');
    $('indices').innerHTML = d.indices.map(indiceCard).join('') || '<span class="caption">Nenhum índice encontrado.</span>';
  } catch (e) {
    $('indices').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`;
  }
}

$('btn-refresh').onclick = load;

$('btn-search').onclick = async () => {
  const query = $('q').value.trim();
  if (!query) return showToast('Digite uma pergunta', 'error');
  const box = $('resultados');
  box.innerHTML = '<span class="caption">Buscando…</span>';
  try {
    const r = await hub.post('/infra/search/test', { query, k: Number($('k').value) || 6 });
    if (r.erro) { box.innerHTML = `<span style="color:var(--danger)">${fmt.esc(r.erro)}</span>`; return; }
    if (!r.resultados.length) { box.innerHTML = '<span class="caption">Nenhum trecho encontrado.</span>'; return; }
    box.innerHTML = r.resultados.map((res, i) => `
      <div class="busca-res">
        <div class="busca-res__head">
          <span class="badge badge--neutral">#${i + 1}</span>
          <span class="mono caption">${fmt.esc(res.fonte)}</span>
          <span class="badge badge--active" title="pontuação combinada (palavra-chave + significado)">score ${res.score}</span>
        </div>
        <p class="busca-res__txt">${fmt.esc(res.trecho)}…</p>
      </div>`).join('');
  } catch (e) { box.innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`; }
};

$('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('btn-search').click(); });

load();
