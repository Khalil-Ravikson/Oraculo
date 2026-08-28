/* home.js — painel do Hub (Plano B §D). Métricas rápidas. */
import { api } from '/static/js/core/api-client.js';
import { fmt } from '/static/js/core/format.js';

const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };

async function carregar() {
  try {
    const [sys, rag, users] = await Promise.allSettled([
      api.get('/system'),
      api.get('/rag/status'),
      api.get('/users/?por_pag=1'),
    ]);

    if (rag.status === 'fulfilled') {
      set('s-chunks', fmt.num(rag.value.chunks_count ?? 0));
      set('s-msgs', fmt.num(rag.value.messages_today ?? 0));
      const hr = rag.value.cache_hit_rate;
      set('s-cache', hr != null ? Math.round(hr * 100) + '%' : '—');
    }
    if (users.status === 'fulfilled') set('s-users', fmt.num(users.value.total ?? 0));

    if (sys.status === 'fulfilled') {
      const note = document.getElementById('maint-note');
      if (sys.value.manutencao) {
        note.innerHTML = '<span class="badge badge--warn">manutenção ativa</span> — respostas LLM bloqueadas para todos.';
      } else {
        note.textContent = 'Sistema operando normalmente.';
      }
    }
  } catch (e) {
    document.getElementById('maint-note').textContent = 'Falha ao carregar métricas: ' + e.message;
  }
}

carregar();
