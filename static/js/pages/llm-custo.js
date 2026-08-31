/* llm-custo.js — Custo & Provedores (Hub v2 Sprint 7): tendência (sparklines),
   disjuntor traduzido + resetar, cotação Real/Manual, custo por assunto em barras. */
import { showToast } from '/static/js/core/toast.js';
import { confirmar } from '/static/js/core/modal.js';
import { fmt } from '/static/js/core/format.js';
import { hub } from '/static/js/core/api-client.js';
import { Glossario } from '/static/js/core/glossario.js';
import { sparkline, meter } from '/static/js/components/sparkline.js';

const $ = (id) => document.getElementById(id);
const rota = (r) => Glossario.rotulo('rota:' + r, r);

const CIRCUITO = {
  fechado: ['badge--ok', 'Operacional'],
  meio_aberto: ['badge--warn', 'Testando recuperação'],
  aberto: ['badge--danger', 'Bloqueado por falhas'],
};

async function carregar() {
  try {
    const d = await hub.get('/llm-custo/data?horas=24');

    $('prov-atual').textContent = d.provider_global_ativo || '—';
    $('prov-select').innerHTML = (d.provedores_opcoes || ['gemini']).map((p) =>
      `<option ${p === d.provider_global_ativo ? 'selected' : ''}>${fmt.esc(p)}</option>`).join('');

    const origem = { manual: 'fixada', ao_vivo: 'cotação real', padrao_fixo: 'padrão' }[d.taxa_brl_origem] || d.taxa_brl_origem || '';
    $('brl-atual').textContent = `R$ ${(d.taxa_brl ?? 5.4).toFixed(2)} · ${origem}`;
    const manual = d.taxa_brl_origem === 'manual';
    $('brl-modo-auto').setAttribute('aria-pressed', String(!manual));
    $('brl-modo-manual').setAttribute('aria-pressed', String(manual));
    $('brl-manual-box').hidden = !manual;

    renderResumo(d);
    renderRegistry(d);

    const brl = (u) => fmt.brl((Number(u) || 0) * (d.taxa_brl ?? 5.4));
    $('por-provider').innerHTML = (d.por_provider || []).map((p) =>
      `<tr><td>${fmt.esc(p.provider)}</td><td class="num">${fmt.num(p.chamadas)}</td><td class="num">${fmt.num(p.tokens ?? 0)}</td><td class="num">${fmt.usd(p.custo_usd)}</td><td class="num">${brl(p.custo_usd)}</td></tr>`).join('')
      || '<tr><td colspan="5" class="table__empty">Sem dados nas últimas 24h.</td></tr>';

    renderRotas(d, brl);

    const cache = d.cache || {};
    $('cache-rota').innerHTML = Object.entries(cache.por_rota || {}).map(([r, n]) =>
      `<tr><td>${fmt.esc(rota(r))}</td><td class="num">${n}</td><td class="num">${fmt.duracao((cache.ttl_por_rota || {})[r] || 0)}</td><td class="num">${(cache.threshold_por_rota || {})[r] ?? '—'}</td><td><button class="btn btn--sm btn--danger" data-cr="${fmt.esc(r)}">Limpar</button></td></tr>`).join('')
      || '<tr><td colspan="5" class="table__empty">Cache vazio.</td></tr>';
    $('cache-rota').querySelectorAll('[data-cr]').forEach((b) => b.onclick = async () => {
      const r = await fetch('/api/admin/cache?rota=' + encodeURIComponent(b.dataset.cr), { method: 'DELETE', credentials: 'same-origin' }).then((x) => x.json());
      showToast(`${r.deleted ?? 0} entradas de ${rota(b.dataset.cr)} removidas`); carregar();
    });
  } catch (e) { showToast('Erro na telemetria: ' + e.message, 'error'); }
}

function renderResumo(d) {
  const r = d.resumo || {};
  const serie = d.serie || [];
  const col = (l, v, key, isPct) => {
    const vals = serie.map((s) => s[key]).filter((x) => x != null);
    const spark = vals.length > 1 ? sparkline(vals, { w: 120 }) : '';
    const bar = isPct ? `<div class="metric__foot">${meter(Number(v) || 0, { cor: (Number(v) || 0) < 30 ? 'var(--warn)' : 'var(--ok)' })}</div>` : '';
    return `<div class="col-4"><div class="card metric">
      <span class="metric__label">${l}</span>
      <span class="metric__value">${v}</span>
      <div class="metric__foot">${spark}</div>${bar}
    </div></div>`;
  };
  $('resumo').innerHTML = [
    col('mensagens', fmt.num(r.total_msgs ?? 0), 'msgs'),
    col('tokens', fmt.num(r.tokens_total ?? 0), 'tokens'),
    col('custo BRL', fmt.brl(d.custo_brl_total ?? 0), 'custo_usd'),
    col('latência média', (r.latencia_media_ms ?? 0) + ' ms', 'latencia_ms'),
    col('cache hit', (r.cache_hit_pct ?? 0) + '%', 'cache_hit_pct', true),
    col('custo USD', fmt.usd(r.custo_usd ?? 0), 'custo_usd'),
  ].join('');
}

function renderRegistry(d) {
  $('prov-registry').innerHTML = (d.provider_registry || []).map((p) => {
    const cb = (d.circuit_breaker || []).find((c) => c.provider === p.nome) || {};
    const cred = p.saude === true ? '<span class="badge badge--ok">definida</span>'
      : p.saude === false ? '<span class="badge badge--danger">sem credencial</span>'
      : '<span class="badge badge--unknown">—</span>';
    const [cls, txt] = CIRCUITO[cb.estado] || ['badge--neutral', cb.estado || '—'];
    const bloqueado = cb.estado === 'aberto' || cb.estado === 'meio_aberto';
    return `<tr>
      <td>${fmt.esc(p.nome)}</td>
      <td>${cred}</td>
      <td><span class="badge ${cls} status-pill">${txt}</span></td>
      <td class="num">${cb.falhas ?? 0}</td>
      <td>${bloqueado ? `<button class="btn btn--sm" data-reset="${fmt.esc(p.nome)}">Resetar circuito</button>` : ''}</td>
    </tr>`;
  }).join('');
  $('prov-registry').querySelectorAll('[data-reset]').forEach((b) => b.onclick = async () => {
    try { await hub.post('/llm/circuit/reset', { provider: b.dataset.reset }); showToast(`Circuito de ${b.dataset.reset} zerado`); carregar(); }
    catch (e) { showToast(e.message, 'error'); }
  });
}

function renderRotas(d, brl) {
  const rotas = (d.por_rota || []).slice().sort((a, b) => (b.custo_usd || 0) - (a.custo_usd || 0));
  const max = Math.max(...rotas.map((r) => r.custo_usd || 0), 0.0001);
  $('rota-barras').innerHTML = rotas.slice(0, 8).map((r) => `
    <div class="rota-barra">
      <span class="rota-barra__label">${fmt.esc(rota(r.rota))}</span>
      <span class="rota-barra__track"><span class="rota-barra__fill" style="width:${Math.round(100 * (r.custo_usd || 0) / max)}%"></span></span>
      <span class="rota-barra__val mono">${brl(r.custo_usd)}</span>
    </div>`).join('') || '<span class="caption">Sem dados.</span>';
  $('por-rota').innerHTML = rotas.map((r) =>
    `<tr><td>${fmt.esc(rota(r.rota))}</td><td class="num">${fmt.num(r.chamadas)}</td><td class="num">${fmt.usd(r.custo_usd)}</td><td class="num">${brl(r.custo_usd)}</td><td class="num">${r.latencia_media_ms ?? '—'}</td></tr>`).join('')
    || '<tr><td colspan="5" class="table__empty">Sem dados.</td></tr>';
}

async function carregarPrecos() {
  try {
    const d = await hub.get('/llm-pricing/data');
    $('pricing').innerHTML = (d.precos || []).map((p, i) => `<tr data-i="${i}">
      <td>${fmt.esc(p.provider)}</td><td class="mono">${fmt.esc(p.modelo)}</td>
      <td class="num"><input class="input" style="max-width:90px" type="number" step="0.01" value="${p.input_por_1m}" data-f="input_por_1m"></td>
      <td class="num"><input class="input" style="max-width:90px" type="number" step="0.01" value="${p.output_por_1m}" data-f="output_por_1m"></td>
      <td class="num"><input class="input" style="max-width:90px" type="number" step="0.01" value="${p.cache_por_1m ?? ''}" data-f="cache_por_1m"></td>
      <td><button class="btn btn--primary btn--sm" data-save="${i}">Salvar</button></td></tr>`).join('');
    $('pricing').querySelectorAll('[data-save]').forEach((b) => b.onclick = async () => {
      const tr = b.closest('tr');
      const body = { provider: tr.children[0].textContent, modelo: tr.children[1].textContent };
      tr.querySelectorAll('input[data-f]').forEach((el) => { body[el.dataset.f] = el.value === '' ? null : Number(el.value); });
      try { await hub.post('/llm-pricing', body); showToast(`Preço ${body.provider}/${body.modelo} salvo`); }
      catch (e) { showToast(e.message, 'error'); }
    });
  } catch (e) { showToast(e.message, 'error'); }
}

$('prov-troca').onclick = async () => {
  const p = $('prov-select').value;
  if (!await confirmar({ titulo: 'Trocar provedor', corpo: `Todas as chamadas passam a usar "${p}". Custo e qualidade mudam.`, acao: 'Trocar' })) return;
  try { await hub.post('/llm/provider', { provider: p }); showToast(`Provedor ativo: ${p}`); carregar(); }
  catch (e) { showToast(e.message, 'error'); }
};
$('brl-modo-manual').onclick = () => { $('brl-manual-box').hidden = false; $('brl-modo-manual').setAttribute('aria-pressed', 'true'); $('brl-modo-auto').setAttribute('aria-pressed', 'false'); };
$('brl-modo-auto').onclick = async () => {
  await fetch('/hub/llm-custo/brl-rate/auto', { method: 'POST', credentials: 'same-origin' });
  showToast('Usando cotação real'); carregar();
};
$('brl-save').onclick = async () => {
  try {
    await fetch('/hub/llm-custo/brl-rate', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ taxa: Number($('brl-input').value) }) });
    showToast('Cotação fixada'); carregar();
  } catch (e) { showToast(e.message, 'error'); }
};

carregar();
carregarPrecos();
