/**
 * My Agent Memory — JS HTTP client for Hanako MemoryProvider interface.
 *
 * Calls my-agent-memory REST API (serve.py) via HTTP fetch.
 * Replaces the old CLI subprocess approach for better performance.
 *
 * Duck-type interface:
 *   prefetch(query)     → string   per-turn memory recall
 *   systemPromptBlock() → string   hot layer for system prompt
 *   sync(user, asst)    → void     post-turn sync (no-op, server handles it)
 *   onSessionEnd()      → void     session cleanup
 *
 * Extended write methods:
 *   saveMemory(content, title, tags, scope, memoryType) → object
 *   pinMemory(id)       → object
 *   unpinMemory(id)     → object
 *   shareMemory(id)     → object
 *   unshareMemory(id)   → object
 *   archiveMemory(id)   → object
 *   searchMemory(query, opts) → array
 *   hybridSearch(query, opts) → array
 */

const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';
const TIMEOUT_MS = 10000;

async function apiGet(base, path, params = {}) {
  const url = new URL(path, base);
  for (const [k, v] of Object.entries(params)) {
    if (v != null) url.searchParams.set(k, String(v));
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url.toString(), { signal: controller.signal });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function apiPost(base, path, body = {}) {
  const url = new URL(path, base);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url.toString(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function apiPut(base, path, body = {}) {
  const url = new URL(path, base);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url.toString(), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function formatResults(results, agentId) {
  if (!results || !Array.isArray(results) || results.length === 0) return '';
  const lines = [];
  for (const r of results) {
    const title = r.title || '(no title)';
    const content = (r.content || '').substring(0, 150);
    const source = r.owner_agent || '';
    const marker = r.is_pinned ? '📌 ' : '';
    const idTag = `[#${r.id}]`;
    if (source && source !== agentId) {
      lines.push(`- ${idTag} ${marker}**${title}** [${source}]: ${content}`);
    } else {
      lines.push(`- ${idTag} ${marker}**${title}**: ${content}`);
    }
  }
  return lines.join('\n');
}

export class HanakoProvider {
  constructor(config = {}) {
    this.agentId = config.agent_id || 'hanako';
    this.baseUrl = (config.base_url || DEFAULT_BASE_URL).replace(/\/+$/, '');
  }

  // ── MemoryProvider interface ──────────────────────────────

  async prefetch(query) {
    const data = await apiGet(this.baseUrl, '/api/hybrid', {
      q: query, limit: 5, agent: this.agentId,
    });
    if (!data || !Array.isArray(data)) return '';
    return formatResults(data, this.agentId);
  }

  async systemPromptBlock() {
    const data = await apiGet(this.baseUrl, '/api/system-prompt', {
      agent: this.agentId,
    });
    return data?.content || '';
  }

  sync(_userMsg, _asstMsg) {
    // Server-side auto-extract handles this via the REST API.
    // No client-side action needed.
  }

  onSessionEnd() {
    // No-op. Session-end extraction is handled server-side.
  }

  // ── Write operations ─────────────────────────────────────

  async saveMemory(content, title = '', tags = [], scope = 'private', memoryType = '') {
    const body = { content };
    if (title) body.title = title;
    if (tags.length) body.tags = tags;
    if (scope) body.scope = scope;
    if (memoryType) body.memory_type = memoryType;
    return apiPost(this.baseUrl, '/api/entries', body);
  }

  async pinMemory(id) {
    return apiPost(this.baseUrl, `/api/entries/${id}/pin`);
  }

  async unpinMemory(id) {
    return apiPost(this.baseUrl, `/api/entries/${id}/unpin`);
  }

  async shareMemory(id) {
    return apiPost(this.baseUrl, `/api/entries/${id}/share`);
  }

  async unshareMemory(id) {
    return apiPost(this.baseUrl, `/api/entries/${id}/unshare`);
  }

  async archiveMemory(id) {
    return apiPost(this.baseUrl, `/api/entries/${id}/archive`);
  }

  async updateMemory(id, fields = {}) {
    return apiPut(this.baseUrl, `/api/entries/${id}`, fields);
  }

  async searchMemory(query, opts = {}) {
    const data = await apiGet(this.baseUrl, '/api/search', {
      q: query,
      limit: opts.limit || 10,
      agent: opts.agent || this.agentId,
      scope: opts.scope,
      memory_type: opts.memoryType,
    });
    return data || [];
  }

  async hybridSearch(query, opts = {}) {
    const data = await apiGet(this.baseUrl, '/api/hybrid', {
      q: query,
      limit: opts.limit || 10,
      agent: opts.agent || '*',
      scope: opts.scope,
      memory_type: opts.memoryType,
      rerank: opts.rerank ? 'true' : '',
    });
    return data || [];
  }

  async getStats() {
    return apiGet(this.baseUrl, '/api/stats');
  }

  async dream(dryRun = true) {
    return apiPost(this.baseUrl, '/api/dreaming', { dry_run: dryRun });
  }
}
