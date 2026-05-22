/**
 * My Agent Memory — JS wrapper for Hanako MemoryProvider interface.
 *
 * Bridges Hanako's Node.js MemoryProvider duck-type interface to the
 * my-agent-memory Python CLI via subprocess.
 *
 * Duck-type interface:
 *   prefetch(query)    → string    per-turn memory recall
 *   systemPromptBlock() → string   hot layer for system prompt
 *   sync(user, asst)   → void      post-turn sync
 *   onSessionEnd()     → void      session cleanup
 *
 * Extended write methods (agent can pin/share/save from conversation):
 *   saveMemory(content, title, tags, scope) → object
 *   pinMemory(id)     → object
 *   shareMemory(id)   → object
 *   unpinMemory(id)   → object
 *   unshareMemory(id) → object
 */

import { execSync } from 'node:child_process';

const CLI = 'my-agent-memory';
const DB_PATH = 'E:/hermes/hermes-data/memories/memory_v2.db';
const BASE_ARGS = `--db-path "${DB_PATH}"`;

function sh(cmd) {
  try {
    const out = execSync(cmd, {
      encoding: 'utf-8',
      timeout: 15000,
      maxBuffer: 1024 * 512,
      windowsHide: true,
    });
    return out.trim();
  } catch (err) {
    console.warn('[my-agent-memory] CLI failed:', cmd.substring(0, 80), '—', err.message);
    return null;
  }
}

function shJson(cmd) {
  const raw = sh(cmd);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function esc(s) {
  return s.replace(/"/g, '\\"');
}

export class HanakoProvider {
  constructor(config) {
    this.agentId = config.agent_id || 'hanako';
    this.dbPath = config.db_path || DB_PATH;
    this._env = { ...process.env, HERMES_AGENT_ID: this.agentId };
  }

  _sh(cmd) {
    try {
      const out = execSync(cmd, {
        encoding: 'utf-8',
        timeout: 15000,
        maxBuffer: 1024 * 512,
        windowsHide: true,
        env: this._env,
      });
      return out.trim();
    } catch (err) {
      console.warn('[my-agent-memory] CLI failed:', cmd.substring(0, 80), '—', err.message);
      return null;
    }
  }

  _shJson(cmd) {
    const raw = this._sh(cmd);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }

  // ── MemoryProvider interface ──────────────────────────────

  prefetch(query) {
    const results = this._shJson(
      `${CLI} ${BASE_ARGS} hybrid "${esc(query)}" --agent ${this.agentId} --limit 5`
    );
    if (!results || !Array.isArray(results) || results.length === 0) return '';

    const lines = [];
    for (const r of results) {
      const title = r.title || '(no title)';
      const content = (r.content || '').substring(0, 150);
      const source = r.owner_agent || '';
      const marker = r.is_pinned ? '📌 ' : '';
      const idTag = `[#${r.id}]`;
      if (source && source !== this.agentId) {
        lines.push(`- ${idTag} ${marker}**${title}** [${source}]: ${content}`);
      } else {
        lines.push(`- ${idTag} ${marker}**${title}**: ${content}`);
      }
    }
    return lines.join('\n');
  }

  systemPromptBlock() {
    return this._sh(`${CLI} ${BASE_ARGS} system-prompt --agent ${this.agentId}`) || '';
  }

  sync(_userMsg, _asstMsg) {}

  onSessionEnd() {}

  // ── Write operations (agent can call from conversation) ───

  saveMemory(content, title = '', tags = [], scope = 'private') {
    const tagStr = Array.isArray(tags) ? tags.join(',') : '';
    const result = this._shJson(
      `${CLI} ${BASE_ARGS} save "${esc(content)}" --title "${esc(title)}" --tags "${esc(tagStr)}" --scope ${scope}`
    );
    return result;
  }

  pinMemory(id) {
    return this._shJson(`${CLI} ${BASE_ARGS} pin ${id}`);
  }

  unpinMemory(id) {
    return this._shJson(`${CLI} ${BASE_ARGS} unpin ${id}`);
  }

  shareMemory(id) {
    return this._shJson(`${CLI} ${BASE_ARGS} share ${id}`);
  }

  unshareMemory(id) {
    return this._shJson(`${CLI} ${BASE_ARGS} unshare ${id}`);
  }
}
