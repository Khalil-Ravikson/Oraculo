/* infra-health.js — Saúde do Sistema (Hub v2 Sprint 7). */
import { fmt } from '/static/js/core/format.js';
import { showToast } from '/static/js/core/toast.js';
import { hub } from '/static/js/core/api-client.js';
import { Glossario } from '/static/js/core/glossario.js';

const $ = (id) => document.getElementById(id);

const PILL = {
  operacional: 'badge--ok', 'não monitorado': 'badge--unknown', desconhecido: 'badge--unknown',
  'com erro': 'badge--danger', erro: 'badge--danger', 'sem resposta': 'badge--warn',
  'bloqueado por falhas': 'badge--danger', 'testando recuperação': 'badge--warn',
};
const pill = (estado) => `<span class="badge ${PILL[estado] || 'badge--neutral'} status-pill">${fmt.esc(estado)}</span>`;

function linha(nome, estado, detalhe = '') {
  return `<div class="health-linha">
    <span>${fmt.esc(nome)}</span>
    <span>${pill(estado)}${detalhe ? ` <span class="caption">${fmt.esc(detalhe)}</span>` : ''}</span>
  </div>`;
}

async function load() {
  try {
    const d = await hub.get('/health');

    // resumo — conta problemas
    const problemas = [];
    d.provedores.filter((p) => p.circuito_raw === 'aberto').forEach((p) => problemas.push(`${p.nome}: circuito bloqueado`));
    d.provedores.filter((p) => p.ativo && p.credencial === false).forEach((p) => problemas.push(`${p.nome} (ativo) sem credencial`));
    d.componentes.filter((c) => c.estado === 'com erro').forEach((c) => problemas.push(`${c.id}: com erro`));
    if (d.bancos.cache.estado === 'erro') problemas.push('cache em memória inacessível');
    if (d.bancos.banco.estado === 'erro') problemas.push('banco principal inacessível');
    if (d.filas.estado !== 'operacional') problemas.push('filas de processamento sem resposta');

    $('resumo').innerHTML = problemas.length
      ? `<div class="badge badge--danger status-pill">${problemas.length} ponto(s) de atenção</div>
         <ul class="health-problemas">${problemas.map((p) => `<li>${fmt.esc(p)}</li>`).join('')}</ul>`
      : '<div class="badge badge--ok status-pill">Tudo operacional</div>';

    $('b-provedores').innerHTML = d.provedores.map((p) =>
      linha(p.nome + (p.ativo ? ' (ativo)' : ''), p.circuito, p.credencial === false ? 'sem credencial' : `${p.falhas} falha(s)`)).join('');

    $('b-infra').innerHTML = [
      linha('Cache em memória', d.bancos.cache.estado, `${d.bancos.cache.memoria_mb ?? '?'} MB · hit ${d.bancos.cache.hit_rate ?? '?'}%`),
      linha('Banco principal', d.bancos.banco.estado, `${d.bancos.banco.conexoes ?? '?'}/${d.bancos.banco.max_conexoes ?? '?'} conexões`),
      linha('Filas de processamento', d.filas.estado, `${(d.filas.workers || []).length} worker(s)`),
    ].join('');

    $('b-componentes').innerHTML = d.componentes.map((c) =>
      linha(Glossario.rotulo('node:' + c.id, c.id), c.estado, c.detalhe || '')).join('');

    $('b-mcp').innerHTML = d.mcp.length
      ? d.mcp.map((m) => linha(m.nome, m.habilitado ? 'operacional' : 'desligado',
          m.latency_ms != null ? `${m.latency_ms} ms · ${m.ferramentas} ferramenta(s)` : 'nunca testado')).join('')
      : '<span class="caption">Nenhum servidor MCP cadastrado.</span>';

    $('b-flags').innerHTML = d.flags_laboratorio.length
      ? d.flags_laboratorio.map((f) =>
          `<span class="badge ${f.ativa ? 'badge--warn' : 'badge--neutral'}" style="margin:2px" data-tech="${fmt.esc(f.chave)}">${fmt.esc(Glossario.rotulo(f.chave, f.chave))}: ${f.ativa ? 'ligada' : 'desligada'}</span>`).join('')
      : '<span class="caption">Nenhuma.</span>';
  } catch (e) {
    $('resumo').innerHTML = `<span style="color:var(--danger)">${fmt.esc(e.message)}</span>`;
  }
}

$('btn-refresh').onclick = load;
load();
