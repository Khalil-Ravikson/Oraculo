/* llm-custo.js — telemetria de custo, providers, circuit breaker, preços. */
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';

const $ = (id) => document.getElementById(id);
const j = (path, opts) => fetch(path, opts).then(r => r.json());

async function carregar() {
  try {
    const d = await j('/hub/llm-custo/data?horas=24');
    $('prov-atual').textContent = d.provider_global_ativo || '—';
    $('prov-select').value = d.provider_global_ativo || 'gemini';
    const origem = { manual: 'manual', ao_vivo: 'ao vivo', padrao_fixo: 'padrão fixo' }[d.taxa_brl_origem] || d.taxa_brl_origem || '';
    $('brl-atual').textContent = `${(d.taxa_brl ?? 5.4).toFixed(2)} · ${origem}`;

    const r = d.resumo || {};
    $('resumo').innerHTML = [
      ['mensagens', fmt.num(r.total_msgs ?? 0)],
      ['tokens', fmt.num(r.tokens_total ?? 0)],
      ['custo USD', fmt.usd(r.custo_usd ?? 0)],
      ['custo BRL', fmt.brl(d.custo_brl_total ?? 0)],
      ['latência média', (r.latencia_media_ms ?? 0) + ' ms'],
      ['cache hit', (r.cache_hit_pct ?? 0) + '%'],
    ].map(([l, v]) => `<div class="col-4"><div class="card card--stat"><div class="stat__value tabular">${v}</div><div class="stat__label">${l}</div></div></div>`).join('');

    $('prov-registry').innerHTML = (d.provider_registry || []).map(p => {
      const cb = (d.circuit_breaker || []).find(c => c.provider === p.nome) || {};
      const saude = p.saude === true ? '<span class="badge badge--ok">ok</span>' : p.saude === false ? '<span class="badge badge--danger">sem credencial</span>' : '<span class="badge badge--neutral">—</span>';
      const est = { fechado: 'badge--ok', meio_aberto: 'badge--warn', aberto: 'badge--danger' }[cb.estado] || 'badge--neutral';
      return `<tr><td>${p.nome}</td><td class="mono caption">${p.interface}</td><td>${saude}</td><td><span class="badge ${est}">${cb.estado || '—'}</span></td><td class="num">${cb.falhas ?? 0}</td></tr>`;
    }).join('');

    const brl = (u) => fmt.brl((Number(u) || 0) * (d.taxa_brl ?? 5.4));
    $('por-provider').innerHTML = (d.por_provider || []).map(p =>
      `<tr><td>${fmt.esc(p.provider)}</td><td class="num">${fmt.num(p.chamadas)}</td><td class="num">${fmt.num(p.tokens ?? 0)}</td><td class="num">${fmt.usd(p.custo_usd)}</td><td class="num">${brl(p.custo_usd)}</td></tr>`).join('')
      || '<tr><td colspan="5" class="table__empty">Sem dados nas últimas 24h.</td></tr>';
    $('por-rota').innerHTML = (d.por_rota || []).map(p =>
      `<tr><td>${fmt.esc(p.rota)}</td><td class="num">${fmt.num(p.chamadas)}</td><td class="num">${fmt.usd(p.custo_usd)}</td><td class="num">${brl(p.custo_usd)}</td><td class="num">${p.latencia_media_ms ?? '—'}</td></tr>`).join('')
      || '<tr><td colspan="5" class="table__empty">Sem dados.</td></tr>';

    const cache = d.cache || {};
    $('cache-rota').innerHTML = Object.entries(cache.por_rota || {}).map(([rota, n]) =>
      `<tr><td>${fmt.esc(rota)}</td><td class="num">${n}</td><td class="num">${fmt.duracao((cache.ttl_por_rota || {})[rota] || 0)}</td><td class="num">${(cache.threshold_por_rota || {})[rota] ?? '—'}</td><td><button class="btn btn--sm btn--danger" data-cr="${rota}">Limpar</button></td></tr>`).join('')
      || '<tr><td colspan="5" class="table__empty">Cache vazio.</td></tr>';
    $('cache-rota').querySelectorAll('[data-cr]').forEach(b => b.onclick = async () => {
      const r = await j('/api/admin/cache?rota=' + encodeURIComponent(b.dataset.cr), { method: 'DELETE' });
      showToast(`${r.deleted ?? 0} entradas de ${b.dataset.cr} removidas`); carregar();
    });
  } catch (e) { showToast('Erro na telemetria: ' + e.message, 'error'); }
}

async function carregarPrecos() {
  try {
    const d = await j('/hub/llm-pricing/data');
    $('pricing').innerHTML = (d.precos || []).map((p, i) => `<tr data-i="${i}">
      <td>${fmt.esc(p.provider)}</td><td class="mono">${fmt.esc(p.modelo)}</td>
      <td class="num"><input class="input" style="max-width:90px" type="number" step="0.01" value="${p.input_por_1m}" data-f="input_por_1m"></td>
      <td class="num"><input class="input" style="max-width:90px" type="number" step="0.01" value="${p.output_por_1m}" data-f="output_por_1m"></td>
      <td class="num"><input class="input" style="max-width:90px" type="number" step="0.01" value="${p.cache_por_1m ?? ''}" data-f="cache_por_1m"></td>
      <td><button class="btn btn--primary btn--sm" data-save="${i}">Salvar</button></td></tr>`).join('');
    $('pricing').querySelectorAll('[data-save]').forEach(b => b.onclick = async () => {
      const tr = b.closest('tr');
      const body = { provider: tr.children[0].textContent, modelo: tr.children[1].textContent };
      tr.querySelectorAll('input[data-f]').forEach(el => { body[el.dataset.f] = el.value === '' ? null : Number(el.value); });
      const r = await j('/hub/llm-pricing', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      showToast(r.ok ? `Preço ${body.provider}/${body.modelo} salvo` : (r.error || 'erro'), r.ok ? 'ok' : 'error');
    });
  } catch (e) { showToast(e.message, 'error'); }
}

$('prov-troca').onclick = async () => {
  if (!await confirmar({ titulo: 'Trocar provider', corpo: `Todas as chamadas passam a usar "${$('prov-select').value}". Custo e qualidade mudam.`, acao: 'Trocar' })) return;
  const r = await j('/hub/llm/provider', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: $('prov-select').value }) });
  showToast(r.ok ? `Provider = ${$('prov-select').value}` : (r.error || 'erro'), r.ok ? 'ok' : 'error'); carregar();
};
$('brl-save').onclick = async () => {
  const r = await j('/hub/llm-custo/brl-rate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rate: Number($('brl-input').value) }) });
  showToast(r.ok ? 'Taxa salva' : (r.error || 'erro'), r.ok ? 'ok' : 'error'); carregar();
};
$('brl-auto').onclick = async () => { await j('/hub/llm-custo/brl-rate/auto', { method: 'POST' }); showToast('Usando taxa ao vivo'); carregar(); };

carregar();
carregarPrecos();
