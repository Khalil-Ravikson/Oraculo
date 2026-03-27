// ── Clock ─────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('pt-BR', { hour12: false });
}
updateClock();
setInterval(updateClock, 1000);

// ── Health check ──────────────────────────────────────────
const COLOR = { ok:'green', warn:'amber', error:'red' };

function setStatus(dotId, valId, ok, text) {
  const dot = document.getElementById(dotId);
  const val = document.getElementById(valId);
  if (!dot || !val) return;
  dot.className = 'dot ' + (ok ? 'green' : 'red');
  val.textContent = text;
  val.className   = 'status-value ' + (ok ? 'ok' : 'error');
}

async function checkHealth() {
  try {
    const r    = await fetch('/health');
    const data = await r.json();

    setStatus('dot-redis',     'val-redis',     data.redis,    data.redis    ? 'ONLINE' : 'OFFLINE');
    setStatus('dot-postgres',  'val-postgres',  data.postgres, data.postgres ? 'ONLINE' : 'OFFLINE');
    setStatus('dot-agent',     'val-agent',     data.agente,   data.agente   ? 'PRONTO' : 'INICIANDO...');
    setStatus('dot-gemini',    'val-gemini',    true,          data.modelo || 'gemini-2.0-flash');
    setStatus('dot-evolution', 'val-evolution', true,          'Evolution API');

    // Header dot
    const allOk = data.redis && data.postgres && data.agente;
    const sysDot = document.getElementById('dot-system');
    const sysStatus = document.getElementById('sys-status');
    sysDot.className = 'dot ' + (allOk ? 'green' : (data.redis ? 'amber' : 'red'));
    sysStatus.textContent = allOk ? 'SISTEMAS OPERACIONAIS' : (data.agente ? 'DEGRADADO' : 'INICIANDO');

    // Terminal message
    const msgs = {
      true:  'todos os subsistemas operacionais — pronto para receber mensagens WhatsApp',
      false: 'atenção: um ou mais serviços offline — verificar docker-compose logs',
    };
    document.getElementById('term-msg').textContent = msgs[String(allOk)];

  } catch(e) {
    setStatus('dot-redis',     'val-redis',     false, 'ERRO DE CONEXÃO');
    setStatus('dot-postgres',  'val-postgres',  false, 'ERRO DE CONEXÃO');
    setStatus('dot-agent',     'val-agent',     false, 'ERRO DE CONEXÃO');
    
    const sysDot = document.getElementById('dot-system');
    if (sysDot) sysDot.className = 'dot red';
    
    const sysStatus = document.getElementById('sys-status');
    if (sysStatus) sysStatus.textContent = 'OFFLINE';
    
    const termMsg = document.getElementById('term-msg');
    if (termMsg) termMsg.textContent = 'erro: servidor não responde — verificar se docker está a correr';
  }
}

// ── Card hover terminal feedback ──────────────────────────
document.querySelectorAll('.card').forEach(card => {
  const title = card.querySelector('.card-title')?.textContent?.toLowerCase() || '';
  const url   = card.getAttribute('href') || '';
  card.addEventListener('mouseenter', () => {
    document.getElementById('term-msg').textContent = `navegando para ${url} — ${title}`;
  });
  card.addEventListener('mouseleave', () => {
    document.getElementById('term-msg').textContent = 'sistema pronto — seleccione um módulo';
  });
});

document.querySelectorAll('.quick-link').forEach(link => {
  const url = link.getAttribute('href') || '';
  link.addEventListener('mouseenter', () => {
    document.getElementById('term-msg').textContent = `GET ${url}`;
  });
  link.addEventListener('mouseleave', () => {
    document.getElementById('term-msg').textContent = 'sistema pronto — seleccione um módulo';
  });
});

// ── Boot ──────────────────────────────────────────────────
setTimeout(checkHealth, 2200);
setInterval(checkHealth, 30000);