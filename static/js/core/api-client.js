/* api-client.js — fetch() único do Hub (Plano B §D).
   Injeta o cookie JWT (via credentials), trata 401 (volta pro login) e
   normaliza o erro. Substitui as 8+ implementações levemente diferentes. */

export class ApiError extends Error {
  constructor(status, body) {
    super(ApiError._msg(status, body));
    this.status = status;
    this.body = body;
  }
  static _msg(status, body) {
    if (body && typeof body === 'object') {
      const d = body.detail ?? body;
      if (d && d.mensagem) return d.mensagem;
      if (typeof d === 'string') return d;
      if (body.error) return body.error;
    }
    return `HTTP ${status}`;
  }
  get isConflict() { return this.status === 409; }
}

async function request(method, path, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch('/api/admin' + path, opts);

  if (res.status === 401) {
    window.location.href = '/hub/login';
    throw new ApiError(401, { error: 'Sessão expirada.' });
  }

  let payload = null;
  const text = await res.text();
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }

  if (!res.ok) throw new ApiError(res.status, payload);
  return payload;
}

export const api = {
  get:  (path)       => request('GET', path),
  post: (path, body) => request('POST', path, body ?? {}),
  patch:(path, body) => request('PATCH', path, body ?? {}),
  del:  (path)       => request('DELETE', path),
};

/* ── Cliente do portal (/hub/*) ──────────────────────────────────────────────
   As páginas do Hub têm endpoints próprios sob /hub (não /api/admin). Mesmo
   tratamento de 401 e de erro; devolve o JSON já parseado. */
async function hubRequest(method, path, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch('/hub' + path, opts);
  if (res.status === 401) { window.location.href = '/hub/login'; throw new ApiError(401, { error: 'Sessão expirada.' }); }
  let payload = null;
  const text = await res.text();
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!res.ok) throw new ApiError(res.status, payload);
  if (payload && payload.error) throw new ApiError(res.status, payload);
  return payload;
}

export const hub = {
  get:  (path)       => hubRequest('GET', path),
  post: (path, body) => hubRequest('POST', path, body ?? {}),
  del:  (path, body) => hubRequest('POST', path, body ?? {}),
};

/* testConnection — probe reutilizável (LLM provider, servidor MCP, canal).
   `kind` só documenta a intenção; o endpoint é passado explicitamente.
   Retorna { ok: boolean, mensagem?, latency_ms? } — nunca lança por "conexão
   falhou", só por erro de transporte/permissão. */
export async function testConnection(kind, endpoint, payload = {}) {
  try {
    const r = await hub.post(endpoint, { ...payload, _kind: kind });
    return { ok: r?.ok !== false && !r?.error, ...r };
  } catch (e) {
    if (e instanceof ApiError && e.status !== 401) return { ok: false, mensagem: e.message };
    throw e;
  }
}
