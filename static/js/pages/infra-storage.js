/* infra-storage.js — painel Armazenamento & Cache (Hub v2 Sprint 5). */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { hub } from '/static/js/core/api-client.js';
import { meter } from '/static/js/components/sparkline.js';

const $ = (id) => document.getElementById(id);

function kpi(label, valor, sub = '', extra = '') {
  return `<div class="card metric">
    <span class="metric__label">${label}</span>
    <span class="metric__value">${valor}${sub ? ` <small>${sub}</small>` : ''}</span>
    ${extra}
  </div>`;
}

function pill(ok, txtOk, txtNo) {
  return ok
    ? `<span class="badge badge--ok status-pill">${txtOk}</span>`
    : `<span class="badge badge--warn status-pill">${txtNo}</span>`;
}

async function load() {
  try {
    const d = await hub.get('/infra/storage/data');
    renderRedis(d.redis);
    renderMods(d.modulos, d.persistencia);
    renderConfig(d.config);
    renderSlowlog(d.slowlog);
    renderPg(d.postgres);
  } catch (e) {
    $('redis-kpis').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`;
  }
}

function renderRedis(r) {
  if (r.erro) { $('redis-kpis').innerHTML = `<span style="color:var(--danger)">Cache inacessível: ${fmt.esc(r.erro)}</span>`; return; }
  const memPct = r.maxmemory_mb ? Math.round(100 * r.memoria_usada_mb / r.maxmemory_mb) : null;
  $('redis-kpis').innerHTML = [
    kpi('Memória usada', `${fmt.num(r.memoria_usada_mb)} MB`,
        r.maxmemory_mb ? `de ${fmt.num(r.maxmemory_mb)} MB` : 'sem limite',
        r.maxmemory_mb ? `<div class="metric__foot">${meter(memPct, { cor: memPct > 85 ? 'var(--danger)' : 'var(--signal)' })}</div>` : ''),
    kpi('Taxa de acerto', r.hit_rate == null ? '—' : `${r.hit_rate}%`, 'cache hit',
        r.hit_rate == null ? '' : `<div class="metric__foot">${meter(r.hit_rate, { cor: r.hit_rate < 40 ? 'var(--warn)' : 'var(--ok)' })}</div>`),
    kpi('Clientes conectados', fmt.num(r.clientes)),
    kpi('Operações/seg', fmt.num(r.ops_por_seg)),
    kpi('Chaves', fmt.num(r.keys), `${fmt.num(r.expired)} expiradas`),
    kpi('Política de despejo', r.eviction_policy),
    kpi('Fragmentação', `${r.fragmentacao}×`, r.fragmentacao > 1.5 ? 'alta' : 'ok'),
    kpi('Versão', r.versao, `${r.modo} · ${r.uptime_dias}d no ar`),
  ].join('');
}

function renderMods(mods, p) {
  const modsHtml = mods.length
    ? mods.map((m) => `<span class="badge badge--ok" style="margin:2px">${fmt.esc(m.nome)} <span class="caption">v${fmt.esc(String(m.versao))}</span></span>`).join('')
    : '<span class="caption">Nenhum módulo Redis Stack carregado (só Redis base).</span>';
  const persist = p.erro ? `<span class="caption">${fmt.esc(p.erro)}</span>` : `
    <dl class="kv u-mt-3">
      <dt>Snapshot em disco (RDB)</dt><dd>${pill(p.rdb_ultimo_save_ok, 'ok', 'falhou')}</dd>
      <dt>Log de escrita (AOF)</dt><dd>${p.aof_ativo ? 'ligado' : 'desligado'}</dd>
      <dt>Mudanças desde o último save</dt><dd>${fmt.num(p.rdb_mudancas_desde_save)}</dd>
      <dt>Último save</dt><dd>${p.rdb_ultimo_save_epoch ? fmt.dateTime(new Date(p.rdb_ultimo_save_epoch * 1000).toISOString()) : '—'}</dd>
    </dl>`;
  $('redis-mods').innerHTML = `<div>${modsHtml}</div>${persist}`;
}

function renderConfig(c) {
  if (c.erro) { $('redis-config').innerHTML = `<span class="caption">${fmt.esc(c.erro)}</span>`; return; }
  $('redis-config').innerHTML = `<dl class="kv">${
    Object.entries(c).map(([k, v]) => `<dt>${fmt.esc(k)}</dt><dd>${fmt.esc(String(v) || '—')}</dd>`).join('')
  }</dl>`;
}

function renderSlowlog(rows) {
  if (!rows.length) { $('slowlog').innerHTML = '<span class="caption">Sem comandos lentos registrados.</span>'; return; }
  $('slowlog').innerHTML = `<table class="table"><thead><tr><th>quando</th><th>duração</th><th>comando</th></tr></thead><tbody>${
    rows.map((s) => `<tr>
      <td class="caption">${fmt.dateTime(new Date(s.epoch * 1000).toISOString())}</td>
      <td class="tabular">${s.ms} ms</td>
      <td class="mono">${fmt.esc(s.comando)}</td>
    </tr>`).join('')
  }</tbody></table>`;
}

function renderPg(pg) {
  if (pg.erro) { $('pg-kpis').innerHTML = `<span style="color:var(--danger)">Banco inacessível: ${fmt.esc(pg.erro)}</span>`; return; }
  const connPct = Math.round(100 * pg.conexoes / pg.max_conexoes);
  $('pg-kpis').innerHTML = [
    kpi('Tamanho do banco', pg.tamanho),
    kpi('Conexões', `${pg.conexoes}`, `de ${pg.max_conexoes} · ${pg.conexoes_ativas} ativas`,
        `<div class="metric__foot">${meter(connPct, { cor: connPct > 80 ? 'var(--danger)' : 'var(--signal)' })}</div>`),
    kpi('Versão', pg.versao),
    kpi('Database', pg.database),
  ].join('');
  $('pg-slow').innerHTML = pg.queries_lentas?.length
    ? `<table class="table"><thead><tr><th>consulta</th><th>chamadas</th><th>média</th></tr></thead><tbody>${
        pg.queries_lentas.map((q) => `<tr><td class="mono">${fmt.esc(q.query)}…</td><td class="tabular">${fmt.num(q.calls)}</td><td class="tabular">${q.ms} ms</td></tr>`).join('')
      }</tbody></table>`
    : '';
}

$('btn-refresh').onclick = load;

$('btn-reindex').onclick = async () => {
  $('reindex-status').textContent = 'Recriando…';
  try {
    const r = await hub.post('/infra/redis/recriar-indices', {});
    $('reindex-status').textContent = `Índices: ${(r.indices || []).join(', ') || '(nenhum)'}`;
    showToast('Índices recriados');
    load();
  } catch (e) { $('reindex-status').textContent = e.message; showToast(e.message, 'error'); }
};

load();
