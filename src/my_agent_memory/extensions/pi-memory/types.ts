/** Shared types for my-agent-memory Pi Code extension. */

export interface MemoryEntry {
  id: number;
  content: string;
  title: string;
  tags: string[];
  source: string;
  owner_agent: string;
  scope: string;
  project: string | null;
  memory_type: string;
  state: string;
  is_pinned: boolean;
  access_count: number;
  score: number;
  validation_status: string | null;
  rrf_score?: number;
  rerank_score?: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryStats {
  total: number;
  promoted: number;
  raw: number;
  archived: number;
  by_state: Record<string, number>;
  by_scope: Record<string, number>;
  by_type: Record<string, number>;
  by_agent: Record<string, number>;
  pinned: number;
  open_conflicts: number;
  last_dreaming: string | null;
  db_path: string;
}

export interface DreamReport {
  dry_run: boolean;
  run_at: string;
  total_entries: number;
  candidates: {
    promote: number;
    demote: number;
    archive: number;
    purge: number;
  };
  promoted: number[];
  demoted: number[];
  archived: number[];
  purged: number[];
  conflicts_found: number;
  by_type: Record<string, {
    promoted: number;
    demoted: number;
    archived: number;
    purged: number;
  }>;
}

export interface Conflict {
  id: number;
  entry_a_id: number;
  entry_b_id: number;
  similarity: number;
  reason: string;
  status: string;
}

export interface TagGraphResult {
  total_pairs: number;
  top_pairs: Array<{ tag_a: string; tag_b: string; count: number }>;
}

export interface RelatedTags {
  tag: string;
  related: Array<{ tag: string; count: number }>;
  count: number;
}

export interface ListResult {
  entries: MemoryEntry[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}
