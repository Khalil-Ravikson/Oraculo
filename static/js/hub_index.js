document.addEventListener("DOMContentLoaded", () => {
  const KEY = sessionStorage.getItem('adminKey');
  const h   = { 'X-Admin-Key': KEY, 'Content-Type': 'application/json' };

  async function api(path) {
    try {
      const r = await fetch('/api/admin' + path, { headers: h });
      return r.json();
    } catch { return null; }
  }

  async function loadStats() {
    const sys   = await api('/system');
    const rag   = await api('/rag/status');
    const users = await api('/users/?por_pag=1');

    // status da chain
    const dot   = document.getElementById('chain-dot');
    const label = document.getElementById('chain-label');
    
    if (sys) {
      dot.className   = 'status-dot' + (sys.manutencao ? ' amber' : '');
      label.textContent = sys.manutencao ? 'MANUTENÇÃO' : 'ONLINE';

      const m    = document.getElementById('stat-maint');
      const msub = document.getElementById('stat-maint-sub');
      m.textContent    = sys.manutencao ? '⚠️' : '✅';
      msub.textContent = sys.manutencao ? 'MODO MANUTENÇÃO' : 'Sistema normal';
    } else {
      dot.className   = 'status-dot red';
      label.textContent = 'OFFLINE';
    }

    if (rag) {
      document.getElementById('stat-chunks').textContent = (rag.chunks_count ?? '—').toLocaleString?.() ?? rag.chunks_count;
      document.getElementById('stat-msgs').textContent   = rag.messages_today ?? '—';
      const hitRate = rag.cache_hit_rate;
      document.getElementById('stat-cache').textContent = hitRate != null ? (hitRate * 100).toFixed(0) + '%' : '—';
    }

    if (users) {
      document.getElementById('stat-users').textContent = users.total ?? '—';
    }
  }

  // Tornando a função de logout global para que o onclick do HTML consiga acessá-la
  window.logout = function() {
    sessionStorage.removeItem('adminKey');
    window.location.href = '/hub/login';
  };

  loadStats();
  setInterval(loadStats, 30000);
});