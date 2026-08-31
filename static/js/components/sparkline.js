/* sparkline.js — mini-gráfico de linha inline (Hub v2 Sprint 5+).
   SVG puro, sem lib. `sparkline(valores, {w, h, cor})` -> string SVG. */

export function sparkline(valores, { w = 96, h = 24, cor = 'var(--signal)' } = {}) {
  const v = (valores || []).map(Number).filter((n) => !Number.isNaN(n));
  if (v.length < 2) return `<svg class="spark" width="${w}" height="${h}"></svg>`;
  const min = Math.min(...v), max = Math.max(...v);
  const span = max - min || 1;
  const step = w / (v.length - 1);
  const pts = v.map((n, i) => `${(i * step).toFixed(1)},${(h - ((n - min) / span) * (h - 3) - 1.5).toFixed(1)}`).join(' ');
  const up = v[v.length - 1] >= v[0];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="${up ? cor : 'var(--danger)'}" stroke-width="1.5" stroke-linejoin="round"/>
  </svg>`;
}

/** Barra de progresso simples (cache hit, uso de memória). 0–100. */
export function meter(pct, { cor = 'var(--signal)' } = {}) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return `<span class="meter"><span class="meter__fill" style="width:${p}%;background:${cor}"></span></span>`;
}
