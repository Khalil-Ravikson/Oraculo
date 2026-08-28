/* format.js — formatação de moeda BRL, data, número (Plano B §D).
   Hoje duplicado em vários JS de página. */

const _brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const _num = new Intl.NumberFormat('pt-BR');

export const fmt = {
  brl:  (v) => _brl.format(Number(v) || 0),
  num:  (v) => _num.format(Number(v) || 0),
  usd:  (v) => '$' + (Number(v) || 0).toFixed(4),

  /** ISO -> "27/08/2026 14:32" */
  dateTime: (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return String(iso).slice(0, 19).replace('T', ' ');
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  },

  /** segundos -> "6h" / "45min" / "12s" */
  duracao: (s) => {
    s = Number(s) || 0;
    if (s >= 3600) return Math.round(s / 3600) + 'h';
    if (s >= 60) return Math.round(s / 60) + 'min';
    return s + 's';
  },

  /** escapa texto vindo de dados antes de ir pra innerHTML */
  esc: (s) => String(s ?? '').replace(/[&<>"']/g, (m) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]
  )),
};
