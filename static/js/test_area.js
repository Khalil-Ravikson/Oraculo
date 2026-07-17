// Estado da Aplicação
const S = { key: null };

// Payloads Iniciais para facilitar o teste
const defaultPayloads = {
    'cadastrar_guest': JSON.stringify({
        "nome_completo": "Khalil Alcântara (Visitante)",
        "email": "khalil@guest.com",
        "telefone": "98999999999",
        "motivo_visita": "Conhecer as instalações da Engenharia da Computação."
    }, null, 2),
    
    'enviar_email': JSON.stringify({
        "destinatario": "contato@oraculo.uema.br",
        "assunto": "Aviso de Sistema: Nova Integração",
        "corpo": "A LLM gerou esta resposta e solicitou o envio do e-mail via Tool Calling."
    }, null, 2)
};

// ─── Eventos de Tela ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const toolSelect = document.getElementById('tool-select');
    const payloadInput = document.getElementById('tool-payload');

    // Seta payload padrão
    payloadInput.value = defaultPayloads[toolSelect.value];

    // Muda o textarea quando troca a tool
    toolSelect.addEventListener('change', (e) => {
        payloadInput.value = defaultPayloads[e.target.value];
    });

    // Login via Enter
    document.getElementById('login-key').addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
    });

    // Auto-login se a chave já estiver salva
    const k = sessionStorage.getItem('adminKey');
    if (k) {
        S.key = k;
        document.getElementById('login-screen').style.display = 'none';
        document.getElementById('app').classList.add('visible');
    }
});

// ─── Autenticação ───────────────────────────────────────────────────────────
async function doLogin() {
    const key = document.getElementById('login-key').value.trim();
    if (!key) return;
    try {
        const res = await fetch('/admin/llm-tools/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key })
        });
        if (!res.ok) {
            const err = await res.json();
            document.getElementById('login-err').textContent = err.detail || 'Chave incorreta';
            return;
        }
        sessionStorage.setItem('adminKey', key);
        S.key = key;
        document.getElementById('login-screen').style.display = 'none';
        document.getElementById('app').classList.add('visible');
    } catch(e) {
        document.getElementById('login-err').textContent = 'Erro de conexão';
    }
}

function doLogout() {
    sessionStorage.removeItem('adminKey');
    location.reload();
}

function toast(msg, type = 'ok') {
    const el = document.createElement('div');
    el.className = `toast-item ${type}`;
    el.textContent = msg;
    document.getElementById('toast').appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// ─── Execução da Tool ───────────────────────────────────────────────────────
async function executeTool() {
    const toolName = document.getElementById('tool-select').value;
    const payloadRaw = document.getElementById('tool-payload').value;
    const output = document.getElementById('response-output');
    
    let toolData;

    // Valida o JSON no frontend antes de mandar
    try {
        toolData = JSON.parse(payloadRaw);
    } catch (e) {
        output.textContent = "Erro: O payload precisa ser um JSON válido.\n\n" + e.message;
        output.style.color = "var(--red)";
        toast("JSON inválido", "err");
        return;
    }

    output.textContent = "Processando Tool Call...";
    output.style.color = "var(--txt)";

    try {
        const res = await fetch('/admin/llm-tools/execute', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-Admin-Key': S.key
            },
            body: JSON.stringify({
                tool_name: toolName,
                tool_data: toolData
            })
        });

        const data = await res.json();

        if (res.ok) {
            output.textContent = JSON.stringify(data, null, 2);
            output.style.color = "var(--green)";
            toast("Tool executada com sucesso!");
        } else {
            output.textContent = JSON.stringify(data, null, 2);
            output.style.color = "var(--red)";
            if (res.status === 401) doLogout();
            toast("Falha na execução", "err");
        }

    } catch(e) {
        output.textContent = "Erro de requisição: " + e.message;
        output.style.color = "var(--red)";
        toast("Erro de conexão", "err");
    }
}