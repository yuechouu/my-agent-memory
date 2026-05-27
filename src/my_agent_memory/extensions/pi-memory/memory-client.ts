/**
 * Memory HTTP Client — wraps my-agent-memory REST API.
 */

import type {
  MemoryEntry, MemoryStats, DreamReport, Conflict,
  TagGraphResult, RelatedTags, ListResult,
} from "./types.ts";

export class MemoryClient {
  private baseUrl: string;
  private timeout: number;

  constructor(baseUrl = "http://127.0.0.1:8765", timeout = 10000) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.timeout = timeout;
  }

  private async get<T>(path: string, params?: Record<string, string>): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
      }
    }
    const resp = await fetch(url.toString(), {
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`Memory API ${resp.status}: ${resp.statusText}`);
    return resp.json() as Promise<T>;
  }

  private async post<T>(path: string, body?: unknown): Promise<T> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`Memory API ${resp.status}: ${resp.statusText}`);
    return resp.json() as Promise<T>;
  }

  private async put<T>(path: string, body: unknown): Promise<T> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`Memory API ${resp.status}: ${resp.statusText}`);
    return resp.json() as Promise<T>;
  }

  private async del<T>(path: string): Promise<T> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method: "DELETE",
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`Memory API ${resp.status}: ${resp.statusText}`);
    return resp.json() as Promise<T>;
  }

  // ── CRUD ────────────────────────────────────────────────

  async save(content: string, title = "", tags: string[] = [],
             scope = "private", memoryType = ""): Promise<MemoryEntry> {
    const body: Record<string, unknown> = { content, title, tags, scope };
    if (memoryType) body.memory_type = memoryType;
    return this.post<MemoryEntry>("/api/entries", body);
  }

  async get(id: number): Promise<MemoryEntry> {
    return this.get<MemoryEntry>(`/api/entries/${id}`);
  }

  async update(id: number, fields: Record<string, unknown>): Promise<MemoryEntry> {
    return this.put<MemoryEntry>(`/api/entries/${id}`, fields);
  }

  async archive(id: number): Promise<MemoryEntry> {
    return this.post<MemoryEntry>(`/api/entries/${id}/archive`);
  }

  async delete(id: number): Promise<{ ok: boolean; id: number }> {
    return this.del(`/api/entries/${id}`);
  }

  // ── Lifecycle ───────────────────────────────────────────

  async pin(id: number): Promise<MemoryEntry> {
    return this.post<MemoryEntry>(`/api/entries/${id}/pin`);
  }

  async unpin(id: number): Promise<MemoryEntry> {
    return this.post<MemoryEntry>(`/api/entries/${id}/unpin`);
  }

  async share(id: number): Promise<MemoryEntry> {
    return this.post<MemoryEntry>(`/api/entries/${id}/share`);
  }

  async unshare(id: number): Promise<MemoryEntry> {
    return this.post<MemoryEntry>(`/api/entries/${id}/unshare`);
  }

  // ── Search ──────────────────────────────────────────────

  async search(query: string, filters?: {
    limit?: number; scope?: string; agent?: string; memory_type?: string;
  }): Promise<MemoryEntry[]> {
    const params: Record<string, string> = { q: query };
    if (filters?.limit) params.limit = String(filters.limit);
    if (filters?.scope) params.scope = filters.scope;
    if (filters?.agent) params.agent = filters.agent;
    if (filters?.memory_type) params.memory_type = filters.memory_type;
    const result = await this.get<{ results?: MemoryEntry[] } | MemoryEntry[]>("/api/search", params);
    return Array.isArray(result) ? result : (result.results ?? []);
  }

  async hybrid(query: string, filters?: {
    limit?: number; scope?: string; agent?: string; memory_type?: string; rerank?: boolean;
  }): Promise<MemoryEntry[]> {
    const params: Record<string, string> = { q: query };
    if (filters?.limit) params.limit = String(filters.limit);
    if (filters?.scope) params.scope = filters.scope;
    if (filters?.agent) params.agent = filters.agent;
    if (filters?.memory_type) params.memory_type = filters.memory_type;
    if (filters?.rerank) params.rerank = "true";
    const result = await this.get<{ results?: MemoryEntry[] } | MemoryEntry[]>("/api/hybrid", params);
    return Array.isArray(result) ? result : (result.results ?? []);
  }

  // ── System ──────────────────────────────────────────────

  async systemPromptBlock(agent?: string, maxChars?: number): Promise<string> {
    const params: Record<string, string> = {};
    if (agent) params.agent = agent;
    if (maxChars) params.max_chars = String(maxChars);
    const result = await this.get<{ content: string }>("/api/system-prompt", params);
    return result.content ?? "";
  }

  async stats(): Promise<MemoryStats> {
    return this.get<MemoryStats>("/api/stats");
  }

  async listEntries(filters?: {
    state?: string; scope?: string; memory_type?: string;
    page?: number; limit?: number; query?: string;
  }): Promise<ListResult> {
    const params: Record<string, string> = {};
    if (filters?.state) params.state = filters.state;
    if (filters?.scope) params.scope = filters.scope;
    if (filters?.memory_type) params.memory_type = filters.memory_type;
    if (filters?.page) params.page = String(filters.page);
    if (filters?.limit) params.limit = String(filters.limit);
    if (filters?.query) params.q = filters.query;
    return this.get<ListResult>("/api/entries", params);
  }

  // ── Dreaming & Conflicts ────────────────────────────────

  async dreaming(dryRun = true): Promise<DreamReport> {
    return this.post<DreamReport>("/api/dreaming", { dry_run: dryRun });
  }

  async conflicts(): Promise<Conflict[]> {
    const result = await this.get<{ conflicts?: Conflict[] } | Conflict[]>("/api/conflicts");
    return Array.isArray(result) ? result : (result.conflicts ?? []);
  }

  async resolveConflict(id: number, strategy: string, mergedContent?: string): Promise<Conflict> {
    const body: Record<string, unknown> = { strategy };
    if (mergedContent) body.merged_content = mergedContent;
    return this.post<Conflict>(`/api/conflicts/${id}/resolve`, body);
  }

  // ── TagGraph ────────────────────────────────────────────

  async tagGraph(tag?: string, action = "stats"): Promise<TagGraphResult | RelatedTags> {
    const params: Record<string, string> = { action };
    if (tag) params.tag = tag;
    return this.get("/api/tag-graph", params);
  }
}
