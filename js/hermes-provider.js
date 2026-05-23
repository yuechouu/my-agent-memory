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

import { execFile } from 'node:child_process';

const CLI = 'my-agent-memory';
const DB_PATH = 'E:/hermes/hermes-data/memories/memory_v2.db';
const BASE_ARGS = ['--db-path', DB_PATH];
const TIMEOUT_MS = 15000;

function runCli(args, env) {
  return new Promise((resolve) => {
    execFile(CLI, args, {
      encoding: 'utf-8',
      timeout: TIMEOUT_MS,
      maxBuffer: 1024 * 512,
      windowsHide: true,
      env: env || process.env,
    }, (err, stdout, stderr) => {
      if (err) {
        console.warn('[my-agent-memory] CLI failed:', args.slice(0, 3).join(' '), '—', err.message);
        resolve(null);
        return;
      }
      resolve(stdout.trim());
    });
  });
}

async function runCliJson(args, env) {
  const raw = await runCli(args, env);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function esc(s) {
  return String(s).replace(/"/g, '\\"');
}

export class HanakoProvider {
  constructor(config) {
    this.agentId = config.agent_id || 'hanako';
    this.dbPath = config.db_path || DB_PATH;
    this._baseArgs = ['--db-path', this.dbPath];
    this._env = { ...process.env, HERMES_AGENT_ID: this.agentId };
  }

  // ── MemoryProvider interface ──────────────────────────────

  async prefetch(query) {
    const results = await runCliJson(
      [...this._baseArgs, 'hybrid', query, '--agent', this.agentId, '--limit', '5'],
      this._env,
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

  async systemPromptBlock() {
    return (await runCli([...this._baseArgs, 'system-prompt', '--agent', this.agentId], this._env)) || '';
  }

  sync(_userMsg, _asstMsg) {}

  onSessionEnd() {}

  // ── Write operations (agent can call from conversation) ───

  async saveMemory(content, title = '', tags = [], scope = 'private') {
    const tagStr = Array.isArray(tags) ? tags.join(',') : '';
    const args = [...this._baseArgs, 'save', content];
    if (title) args.push('--title', title);
    if (tagStr) args.push('--tags', tagStr);
    if (scope) args.push('--scope', scope);
    return runCliJson(args, this._env);
  }

  async pinMemory(id) {
    return runCliJson([...this._baseArgs, 'pin', String(id)], this._env);
  }

  async unpinMemory(id) {
    return runCliJson([...this._baseArgs, 'unpin', String(id)], this._env);
  }

  async shareMemory(id) {
    return runCliJson([...this._baseArgs, 'share', String(id)], this._env);
  }

  async unshareMemory(id) {
    return runCliJson([...this._baseArgs, 'unshare', String(id)], this._env);
  }
}
