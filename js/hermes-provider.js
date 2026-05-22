/**
 * Hermes Memory v2 — JS wrapper for Hanako MemoryProvider interface.
 *
 * Bridges Hanako's Node.js MemoryProvider duck-type interface to the
 * hermes-memory Python CLI via subprocess.
 *
 * Config (hermes_v2.json):
 *   {
 *     "module": "E:/hana/hanako-data/providers/hermes-provider.js",
 *     "class": "HanakoProvider",
 *     "agent_id": "hanako",
 *     "db_path": "E:/hermes/hermes-data/memories/memory_v2.db"
 *   }
 */

import { execSync } from 'node:child_process';

const CLI = 'hermes-memory';
const DB_PATH = 'E:/hermes/hermes-data/memories/memory_v2.db';
const AGENT_ID = 'hanako';
const BASE_ARGS = `--db-path "${DB_PATH}"`;

function sh(cmd) {
  try {
    const out = execSync(cmd, {
      encoding: 'utf-8',
      timeout: 15000,
      maxBuffer: 1024 * 512,
      windowsHide: true,
      env: { ...process.env, HERMES_AGENT_ID: AGENT_ID },
    });
    return out.trim();
  } catch (err) {
    console.warn('[hermes-provider] CLI failed:', cmd.substring(0, 80), '—', err.message);
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

export class HanakoProvider {
  constructor(config) {
    this.agentId = config.agent_id || AGENT_ID;
    this.dbPath = config.db_path || DB_PATH;
  }

  /**
   * Per-turn memory recall via hybrid search.
   * Results appended to message context (not system prompt).
   * @param {string} query
   * @returns {string}
   */
  prefetch(query) {
    const q = query.replace(/"/g, '\\"');
    const results = shJson(
      `${CLI} ${BASE_ARGS} hybrid "${q}" --agent ${this.agentId} --limit 5`
    );
    if (!results || !Array.isArray(results) || results.length === 0) return '';

    const lines = [];
    for (const r of results) {
      const title = r.title || '(no title)';
      const content = (r.content || '').substring(0, 150);
      const source = r.owner_agent || '';
      const marker = r.is_pinned ? '📌 ' : '';
      if (source && source !== this.agentId) {
        lines.push(`- ${marker}**${title}** [${source}]: ${content}`);
      } else {
        lines.push(`- ${marker}**${title}**: ${content}`);
      }
    }
    return lines.join('\n');
  }

  /**
   * Hot layer content for system prompt volatile layer.
   * Returns agent-specific + shared entries, sorted by score.
   * Hanako's system_prompt.js truncates to its own token budget.
   * @returns {string}
   */
  systemPromptBlock() {
    return sh(`${CLI} ${BASE_ARGS} system-prompt --agent ${this.agentId}`) || '';
  }

  /**
   * Post-turn sync. Hanako's local experience library manages itself.
   * This provider only handles explicit writes to Hermes shared memory.
   * @param {string} _userMsg
   * @param {string} _asstMsg
   */
  sync(_userMsg, _asstMsg) {
    // No-op: hanako's local experience library is independent.
    // Explicit writes to Hermes go through hermes-memory save CLI.
  }

  /**
   * Session end — optionally trigger dreaming.
   * Note: dreams are typically managed by a separate cron/interval.
   */
  onSessionEnd() {
    // Dreaming is cron-scheduled, not triggered per-session by default.
    // Uncomment below to auto-dream on session end:
    // sh(`hermes-memory dream --execute ${this._baseArgs}`);
  }
}
