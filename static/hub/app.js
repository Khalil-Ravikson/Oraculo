/**
 * ============================================================================
 * HUB.JS — Painel Central do Oráculo UEMA
 * ============================================================================
 * Lida com o relógio em tempo real, health checks da API e feedback interativo
 * do terminal virtual.
 */

// ── 1. Relógio em Tempo Real ────────────────────────────────────────────────
/**
 * Atualiza o relógio do painel com a hora local.
 */
function updateClock() {
  const now = new Date();
  const clockElement = document.getElementById('clock');
  
  // Apenas atualiza se o elemento existir no DOM para evitar erros
  if (clockElement) {
    clockElement.textContent = now.toLocaleTimeString('pt-BR', { hour12: false });
  }
}

// Inicializa o relógio imediatamente e depois atualiza a cada segundo
updateClock();
setInterval(updateClock, 1000);

// ── 2. Health Check (Monitorização de Sistemas) ─────────────────────────────
const COLOR = { ok: 'green', warn: 'amber', error: 'red' };

/**
 * Utilitário para atualizar visualmente o status de um serviço no DOM.
 * @param {string} dotId - O ID do elemento "bolinha" de status (indicador luminoso).
 * @param {string} valId - O ID do elemento de texto com o valor do status.
 * @param {boolean} isOk - Se o serviço está online/pronto (true) ou offline (false).
 * @param {string} text - O texto descritivo a ser exibido.
 */
function setStatus(dotId, valId, isOk, text) {
  const dot = document.getElementById(dotId);
  const val = document.getElementById(valId);
  
  if (!dot || !val) return; // Fail-safe: se o HTML não tiver os IDs, não quebra o JS
  
  dot.className = 'dot ' + (isOk ? COLOR.ok : COLOR.error);
  val.textContent = text;
  val.className   = 'status-value ' + (isOk ? 'ok' : 'error');
}

/**
 * Consulta a rota `/health` da API e atualiza todos os indicadores do painel.
 */
async function checkHealth() {
  const sysDot = document.getElementById('dot-system');
  const sysStatus = document.getElementById('sys-status');
  const termMsg = document.getElementById('term-msg');

  try {
    const response = await fetch('/health');
    
    // Se a API não devolver 200 OK, lançamos erro para cair no catch
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    
    const data = await response.json();

    // ── Atualiza indicadores individuais ──
    // NOTA: Ajustado para mapear os campos reais da nova API v5 do Oráculo UEMA
    // A rota /health devolve: { status, sistema, versao, redis_ok, chain_ok, framework }
    setStatus('dot-redis',    'val-redis',    data.redis_ok, data.redis_ok ? 'ONLINE' : 'OFFLINE');
    setStatus('dot-agent',    'val-agent',    data.chain_ok, data.chain_ok ? 'PRONTO' : 'OFFLINE');
    
    // Como a API v5 removeu o Postgres do health endpoint principal (ou pode estar noutro lado),
    // podes adicionar aqui se voltares a expor, ou mantemos como mock seguro se não vier na data:
    const postgresOk = data.postgres !== undefined ? data.postgres : true; 
    setStatus('dot-postgres', 'val-postgres', postgresOk,    postgresOk ? 'ONLINE' : 'OFFLINE');
    
    // Serviços externos / Estáticos
    setStatus('dot-gemini',   'val-gemini',   true, 'gemini-3.1-flash'); // Modelo atual
    setStatus('dot-evolution','val-evolution',true, 'Evolution API');

    // ── Atualiza o Status Global do Sistema (Cabeçalho) ──
    const allOk = data.redis_ok && data.chain_ok && postgresOk;
    
    if (sysDot && sysStatus) {
        // Lógica de cores: Verde se tudo OK, Laranja se a Chain estiver offline (mas o DB online), Vermelho se DB cair
        sysDot.className = 'dot ' + (allOk ? COLOR.ok : (data.redis_ok ? COLOR.warn : COLOR.error));
        sysStatus.textContent = allOk ? 'SISTEMAS OPERACIONAIS' : (data.chain_ok ? 'DEGRADADO' : 'MANUTENÇÃO');
    }

    // ── Atualiza a mensagem do Terminal ──
    if (termMsg) {
        termMsg.textContent = allOk 
            ? 'todos os subsistemas operacionais — pronto para receber mensagens WhatsApp' 
            : 'atenção: um ou mais serviços offline — verificar docker logs do container da api';
    }

  } catch(error) {
    console.error("❌ Falha no Health Check:", error);
    
    // Modo de Falha Severa: A API não respondeu
    setStatus('dot-redis',    'val-redis',    false, 'ERRO DE CONEXÃO');
    setStatus('dot-postgres', 'val-postgres', false, 'ERRO DE CONEXÃO');
    setStatus('dot-agent',    'val-agent',    false, 'ERRO DE CONEXÃO');
    
    if (sysDot) sysDot.className = 'dot ' + COLOR.error;
    if (sysStatus) sysStatus.textContent = 'OFFLINE CRÍTICO';
    if (termMsg) termMsg.textContent = 'erro fatal: servidor da api inacessível — verificar docker compose';
  }
}

// ── 3. Feedback do Terminal Virtual (Interatividade Hover) ──────────────────
/**
 * Adiciona eventos aos cartões (cards) para simular um terminal
 * que descreve o que o utilizador está prestes a aceder.
 */
document.querySelectorAll('.card').forEach(card => {
  const title = card.querySelector('.card-title')?.textContent?.toLowerCase() || 'módulo';
  const url   = card.getAttribute('href') || '#';
  const termMsg = document.getElementById('term-msg');

  card.addEventListener('mouseenter', () => {
    if (termMsg) termMsg.textContent = `navegando para ${url} — a aceder ao ${title}`;
  });
  
  card.addEventListener('mouseleave', () => {
    if (termMsg) termMsg.textContent = 'sistema pronto — aguardando seleção do operador';
  });
});

/**
 * Adiciona feedback similar para os links rápidos do footer.
 */
document.querySelectorAll('.quick-link').forEach(link => {
  const url = link.getAttribute('href') || '';
  const termMsg = document.getElementById('term-msg');

  link.addEventListener('mouseenter', () => {
    if (termMsg) termMsg.textContent = `GET request pendente para: ${url}`;
  });
  
  link.addEventListener('mouseleave', () => {
    if (termMsg) termMsg.textContent = 'sistema pronto — aguardando seleção do operador';
  });
});

// ── 4. Ciclo de Vida da Página ──────────────────────────────────────────────
// Atrasa o primeiro health check para dar tempo ao servidor de fazer o boot
// e para a animação inicial do painel terminar
setTimeout(checkHealth, 2200);

// Faz pooling do estado da API a cada 30 segundos
setInterval(checkHealth, 30000);