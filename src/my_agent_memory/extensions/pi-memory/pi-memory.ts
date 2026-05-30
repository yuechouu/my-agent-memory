/**
 * Pi Code Extension: my-agent-memory
 *
 * Provides persistent memory capabilities for Pi Coding Agent via
 * my-agent-memory REST API.
 *
 * Three-layer automation:
 *   Layer 1 — Tool descriptions with trigger conditions (LLM自主判断)
 *   Layer 2 — System prompt guidelines (全局记忆使用规则)
 *   Layer 3 — Event fallbacks:
 *     - tool_call: track if agent searched this turn
 *     - input: intercept explicit save intent keywords
 *     - context: fallback prefetch when agent didn't search
 *     - turn_end: auto-extract memories from assistant replies
 *     - session_shutdown: record session end
 *
 * Features:
 *   - 10 LLM-callable memory tools
 *   - Hot layer injection into system prompt
 *   - /memory, /dream, /conflicts slash commands
 *
 * Prerequisites:
 *   my-agent-memory serve --port 8765
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { MemoryClient } from "./memory-client.ts";

// ── Configuration ──────────────────────────────────────────

const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
const DEFAULT_AGENT_ID = "pi";
const DEFAULT_MAX_CHARS = 3000;

// ── TypeBox Schemas ────────────────────────────────────────

const SearchParams = Type.Object({
  query: Type.String({ description: "Search query." }),
  limit: Type.Optional(Type.Number({ description: "Max results (default 5)." })),
  scope: Type.Optional(Type.String({ description: "Filter: private or shared." })),
  memory_type: Type.Optional(Type.String({ description: "Filter: procedural, entity, or knowledge." })),
});

const SaveParams = Type.Object({
  content: Type.String({ description: "The fact to remember." }),
  title: Type.Optional(Type.String({ description: "Short descriptive title." })),
  tags: Type.Optional(Type.String({ description: "Comma-separated tags." })),
  scope: Type.Optional(Type.String({ description: "private or shared (default: private)." })),
  memory_type: Type.Optional(Type.String({ description: "procedural, entity, or knowledge. Auto-detected if omitted." })),
});

const RecallParams = Type.Object({
  query: Type.String({ description: "Structured recall query." }),
  memory_type: Type.Optional(Type.String({ description: "Filter by type." })),
  scope: Type.Optional(Type.String({ description: "Filter by scope." })),
  tags: Type.Optional(Type.String({ description: "Comma-separated tags to filter by." })),
  limit: Type.Optional(Type.Number({ description: "Max results (default 5)." })),
});

const UpdateParams = Type.Object({
  entry_id: Type.Number({ description: "Memory entry ID to update." }),
  content: Type.Optional(Type.String({ description: "New content." })),
  title: Type.Optional(Type.String({ description: "New title." })),
  tags: Type.Optional(Type.String({ description: "New comma-separated tags." })),
});

const ArchiveParams = Type.Object({
  entry_id: Type.Number({ description: "Memory entry ID to archive." }),
});

const PinParams = Type.Object({
  entry_id: Type.Number({ description: "Memory entry ID." }),
  unpin: Type.Optional(Type.Boolean({ description: "Set true to unpin." })),
});

const ListParams = Type.Object({
  state: Type.Optional(Type.String({ description: "Filter: raw, promoted, hot, archived." })),
  scope: Type.Optional(Type.String({ description: "Filter: private, shared, project." })),
  memory_type: Type.Optional(Type.String({ description: "Filter by type." })),
  page: Type.Optional(Type.Number({ description: "Page number (default 1)." })),
  limit: Type.Optional(Type.Number({ description: "Results per page (default 10)." })),
});

const DreamParams = Type.Object({
  dry_run: Type.Optional(Type.Boolean({ description: "Preview only (default true)." })),
});

const ConflictsParams = Type.Object({
  conflict_id: Type.Optional(Type.Number({ description: "Conflict ID to resolve." })),
  strategy: Type.Optional(Type.String({ description: "Resolution: last_write_wins, keep_both, merge, dismiss." })),
  merged_content: Type.Optional(Type.String({ description: "Merged content (for merge strategy)." })),
});

const TagGraphParams = Type.Object({
  tag: Type.Optional(Type.String({ description: "Tag to find related tags for." })),
  action: Type.Optional(Type.String({ description: "related or stats (default: stats)." })),
});

// ── Memory Guidelines (Layer 2) ──────────────────────────────

const MEMORY_GUIDELINES = `
## Memory System
You have access to a persistent memory system. Use it actively.

### When to Search
- User asks about past interactions, preferences, or project details
- Before answering questions that might be in memory

### When to Save
- User shares important preferences, decisions, instructions
- User explicitly says to remember something
- You learn durable facts (not temporary context)

### When to Update
- User corrects previously stored information
- You notice contradictions between conversation and memory

### Memory Types
- procedural: workflows, how-to steps, instructions
- entity: facts about specific things (servers, tools, projects)
- knowledge: general concepts, theories, configurations
`.trim();

// ── Keywords (Layer 3) ─────────────────────────────────────

const SAVE_KEYWORDS = [
  "记住", "记下来", "别忘了", "不要忘记", "记一下", "帮我记", "保存这个", "记录下来",
  "remember this", "save this", "note that", "don't forget", "keep in mind",
];

const MEMORY_KEYWORDS = [
  "记得", "记住", "忘记", "之前", "上次", "以前", "说过", "聊过", "提到", "告诉过",
  "remember", "recall", "before", "previously", "mentioned",
];

// ── Helper ─────────────────────────────────────────────────

function stripEmbedding(entry: Record<string, unknown>): Record<string, unknown> {
  const { embedding, ...rest } = entry;
  return rest;
}

function formatEntry(entry: Record<string, unknown>): string {
  const pin = entry.is_pinned ? "📌 " : "";
  const title = entry.title || "(untitled)";
  const content = String(entry.content || "").slice(0, 120);
  const mtype = entry.memory_type ? ` {${entry.memory_type}}` : "";
  const scope = entry.scope !== "private" ? ` [${entry.scope}]` : "";
  return `[${entry.id}] ${pin}**${title}**${scope}${mtype}\n    ${content}`;
}

// ── Extension Factory ──────────────────────────────────────

export default function piMemoryExtension(pi: ExtensionAPI) {
  // Config from flags
  let baseUrl = DEFAULT_BASE_URL;
  let agentId = DEFAULT_AGENT_ID;
  let maxChars = DEFAULT_MAX_CHARS;
  let autoExtract = true;

  pi.registerFlag("memory-url", { description: "my-agent-memory server URL", type: "string", default: DEFAULT_BASE_URL });
  pi.registerFlag("memory-agent", { description: "Agent ID for memory namespace", type: "string", default: DEFAULT_AGENT_ID });
  pi.registerFlag("memory-max-chars", { description: "Max chars for hot layer injection", type: "string", default: String(DEFAULT_MAX_CHARS) });
  pi.registerFlag("memory-auto-extract", { description: "Auto-extract memories from turns", type: "boolean", default: true });

  let client: MemoryClient;
  let agentSearchedThisTurn = false;
  let saveFlagged = false;

  function getClient(): MemoryClient {
    if (!client) {
      client = new MemoryClient(baseUrl);
    }
    return client;
  }

  // ── Session Start: read flags, create client ──────────

  pi.on("session_start", (_event, ctx) => {
    baseUrl = String(pi.getFlag("memory-url") ?? DEFAULT_BASE_URL);
    agentId = String(pi.getFlag("memory-agent") ?? DEFAULT_AGENT_ID);
    maxChars = Number(pi.getFlag("memory-max-chars") ?? DEFAULT_MAX_CHARS);
    autoExtract = Boolean(pi.getFlag("memory-auto-extract") ?? true);
    client = new MemoryClient(baseUrl);
    // Silently check connectivity
    getClient().stats().catch(() => {});
  });

  // ── Hot Layer + Guidelines Injection ───────────────────

  pi.on("before_agent_start", async (event, ctx) => {
    try {
      const block = await getClient().systemPromptBlock(agentId, maxChars);
      const parts = [event.systemPrompt];
      if (block && block.trim()) parts.push(block);
      parts.push(MEMORY_GUIDELINES);
      return { systemPrompt: parts.join("\n\n") };
    } catch {
      // Memory server not available — inject guidelines only
      return { systemPrompt: event.systemPrompt + "\n\n" + MEMORY_GUIDELINES };
    }
  });

  // ── Layer 3: tool_call — track search state ────────────

  pi.on("tool_call", (event) => {
    if (["memory_search", "memory_recall"].includes(event.toolName)) {
      agentSearchedThisTurn = true;
    }
  });

  // ── Layer 3: input — save intent interception ──────────

  pi.on("input", async (event) => {
    if (SAVE_KEYWORDS.some(kw => event.text.includes(kw))) {
      saveFlagged = true;
    }
    return { action: "continue" as const };
  });

  // ── Layer 3: context — fallback prefetch ───────────────

  pi.on("context", async (event) => {
    // Reset search tracking at turn boundary
    if (agentSearchedThisTurn) {
      agentSearchedThisTurn = false;
      return;
    }
    agentSearchedThisTurn = false;

    // Find last user message
    const messages = event.messages ?? [];
    const lastUserMsg = [...messages].reverse().find((m: any) => m.role === "user");
    const text = lastUserMsg?.content ?? "";
    if (typeof text !== "string" || !MEMORY_KEYWORDS.some(kw => text.includes(kw))) return;

    try {
      const results = await getClient().hybrid(text, { limit: 3 });
      if (results.length > 0) {
        const formatted = results.map(formatEntry).join("\n\n");
        return {
          messages: [...messages, { role: "user" as const, content: `[Memory:]\n${formatted}` }],
        };
      }
    } catch {
      // Best-effort
    }
  });

  // ── Layer 3: turn_end — auto-extract ──────────────────

  pi.on("turn_end", async (event) => {
    if (!autoExtract) return;

    try {
      const lastMsg = event.message;
      if (!lastMsg || lastMsg.type !== "assistant") return;

      const content = typeof lastMsg.content === "string"
        ? lastMsg.content
        : Array.isArray(lastMsg.content)
          ? lastMsg.content.filter((c: any) => c.type === "text").map((c: any) => c.text).join("")
          : "";

      if (content.length < 50) return;

      // Save auto-extracted memory (fire-and-forget)
      getClient().save(content.slice(0, 500), "auto-extract", [], "private", "").catch(() => {});
    } catch {
      // Best-effort
    }

    // Reset turn state
    saveFlagged = false;
    agentSearchedThisTurn = false;
  });

  // ── Layer 3: session_shutdown ─────────────────────────

  pi.on("session_shutdown", async () => {
    try {
      await getClient().save("Session ended", "session-end", ["session"], "private", "");
    } catch {
      // Best-effort
    }
  });

  // ── Tool Registration ─────────────────────────────────

  pi.registerTool({
    name: "memory_search",
    label: "Memory Search",
    description: "Search persistent memory using hybrid FTS5 + vector search. **Use when**: user asks about past interactions, preferences, project details, or before answering questions that might be in memory.",
    promptSnippet: "Search the memory system for relevant information.",
    parameters: SearchParams,
    async execute(_id, params) {
      const results = await getClient().hybrid(params.query, {
        limit: params.limit ?? 5,
        scope: params.scope,
        memory_type: params.memory_type,
      });
      const formatted = results.map(formatEntry).join("\n\n");
      return {
        content: [{ type: "text", text: formatted || "No memories found." }],
        details: { count: results.length },
      };
    },
  });

  pi.registerTool({
    name: "memory_save",
    label: "Memory Save",
    description: "Save a durable fact to persistent memory. **Use when**: user shares important preferences, decisions, instructions, or explicitly says 'remember this'. Do NOT save temporary context.",
    promptSnippet: "Save important information to long-term memory.",
    parameters: SaveParams,
    async execute(_id, params) {
      const tags = params.tags ? params.tags.split(",").map((t: string) => t.trim()).filter(Boolean) : [];
      const entry = await getClient().save(
        params.content,
        params.title ?? "",
        tags,
        params.scope ?? "private",
        params.memory_type ?? "",
      );
      return {
        content: [{ type: "text", text: `Saved memory #${entry.id}: ${entry.title || entry.content.slice(0, 80)}` }],
        details: stripEmbedding(entry as any),
      };
    },
  });

  pi.registerTool({
    name: "memory_recall",
    label: "Memory Recall",
    description: "Structured recall with filters. **Use when**: you need precise filtering by type, scope, or tags. Prefer memory_search for simple queries.",
    parameters: RecallParams,
    async execute(_id, params) {
      const tags = params.tags ? params.tags.split(",").map((t: string) => t.trim()).filter(Boolean) : undefined;
      const results = await getClient().search(params.query, {
        limit: params.limit ?? 5,
        scope: params.scope,
        memory_type: params.memory_type,
      });
      const formatted = results.map(formatEntry).join("\n\n");
      return {
        content: [{ type: "text", text: formatted || "No matching memories." }],
        details: { count: results.length },
      };
    },
  });

  pi.registerTool({
    name: "memory_update",
    label: "Memory Update",
    description: "Update the content, title, or tags of an existing memory entry. **Use when**: user corrects previously stored information or you notice contradictions.",
    parameters: UpdateParams,
    async execute(_id, params) {
      const fields: Record<string, unknown> = {};
      if (params.content) fields.content = params.content;
      if (params.title) fields.title = params.title;
      if (params.tags) fields.tags = params.tags.split(",").map((t: string) => t.trim()).filter(Boolean);
      const entry = await getClient().update(params.entry_id, fields);
      return {
        content: [{ type: "text", text: `Updated memory #${entry.id}` }],
        details: stripEmbedding(entry as any),
      };
    },
  });

  pi.registerTool({
    name: "memory_archive",
    label: "Memory Archive",
    description: "Archive (soft-delete) a memory entry. **Use when**: information is no longer relevant or user asks to forget.",
    parameters: ArchiveParams,
    async execute(_id, params) {
      const entry = await getClient().archive(params.entry_id);
      return {
        content: [{ type: "text", text: `Archived memory #${entry.id}: ${entry.title || entry.content.slice(0, 60)}` }],
        details: stripEmbedding(entry as any),
      };
    },
  });

  pi.registerTool({
    name: "memory_pin",
    label: "Memory Pin",
    description: "Pin a memory so it's never auto-archived and always appears in the hot layer. **Use when**: user emphasizes something is critical and should never be auto-archived. Or unpin it.",
    parameters: PinParams,
    async execute(_id, params) {
      const entry = params.unpin
        ? await getClient().unpin(params.entry_id)
        : await getClient().pin(params.entry_id);
      const action = params.unpin ? "Unpinned" : "Pinned";
      return {
        content: [{ type: "text", text: `${action} memory #${entry.id}` }],
        details: stripEmbedding(entry as any),
      };
    },
  });

  pi.registerTool({
    name: "memory_list",
    label: "Memory List",
    description: "List recent memory entries with optional filters and pagination.",
    parameters: ListParams,
    async execute(_id, params) {
      const result = await getClient().listEntries({
        state: params.state,
        scope: params.scope,
        memory_type: params.memory_type,
        page: params.page ?? 1,
        limit: params.limit ?? 10,
      });
      const formatted = result.entries.map(formatEntry).join("\n");
      const header = `Page ${result.page}/${result.pages} (${result.total} total)`;
      return {
        content: [{ type: "text", text: `${header}\n\n${formatted || "(empty)"}` }],
        details: { total: result.total, page: result.page },
      };
    },
  });

  pi.registerTool({
    name: "memory_dream",
    label: "Memory Dream",
    description: "Run a dreaming lifecycle pass. Promotes popular memories, demotes stale ones. Default is dry-run preview.",
    parameters: DreamParams,
    async execute(_id, params) {
      const report = await getClient().dreaming(params.dry_run ?? true);
      const lines = [
        `Dreaming ${report.dry_run ? "(DRY RUN)" : "(EXECUTED)"}`,
        `Total entries: ${report.total_entries}`,
        `Candidates — promote: ${report.candidates.promote}, demote: ${report.candidates.demote}, archive: ${report.candidates.archive}, purge: ${report.candidates.purge}`,
      ];
      if (!report.dry_run) {
        lines.push(`Applied — promoted: ${report.promoted.length}, demoted: ${report.demoted.length}, archived: ${report.archived.length}`);
      }
      return {
        content: [{ type: "text", text: lines.join("\n") }],
        details: report as any,
      };
    },
  });

  pi.registerTool({
    name: "memory_conflicts",
    label: "Memory Conflicts",
    description: "View open memory conflicts or resolve a specific conflict.",
    parameters: ConflictsParams,
    async execute(_id, params) {
      if (params.conflict_id) {
        const strategy = params.strategy ?? "dismiss";
        const result = await getClient().resolveConflict(params.conflict_id, strategy, params.merged_content);
        return {
          content: [{ type: "text", text: `Resolved conflict #${result.id} with strategy: ${strategy}` }],
          details: result as any,
        };
      }
      const conflicts = await getClient().conflicts();
      if (conflicts.length === 0) {
        return { content: [{ type: "text", text: "No open conflicts." }] };
      }
      const formatted = conflicts.map((c) =>
        `#${c.id}: entries ${c.entry_a_id} ↔ ${c.entry_b_id} (similarity: ${c.similarity?.toFixed(3)}) — ${c.reason}`
      ).join("\n");
      return {
        content: [{ type: "text", text: `Open conflicts:\n${formatted}` }],
        details: { count: conflicts.length },
      };
    },
  });

  pi.registerTool({
    name: "memory_tag_graph",
    label: "Memory Tag Graph",
    description: "Explore tag relationships and co-occurrence patterns. Find what tags relate to a given tag.",
    parameters: TagGraphParams,
    async execute(_id, params) {
      const action = params.action ?? "stats";
      const result = await getClient().tagGraph(params.tag, action);
      if (action === "related" && "related" in result) {
        const lines = [`Tags related to "${result.tag}":`];
        for (const r of (result as any).related) {
          lines.push(`  ${r.tag} (${r.count} co-occurrences)`);
        }
        return { content: [{ type: "text", text: lines.join("\n") }] };
      }
      const stats = result as any;
      const lines = [`Tag graph: ${stats.total_pairs} tag pairs`];
      if (stats.top_pairs?.length) {
        lines.push("Top pairs:");
        for (const p of stats.top_pairs.slice(0, 5)) {
          lines.push(`  ${p.tag_a} ↔ ${p.tag_b} (${p.count})`);
        }
      }
      return { content: [{ type: "text", text: lines.join("\n") }] };
    },
  });

  // ── RAG Tools ─────────────────────────────────────────

  pi.registerTool({
    name: "rag_ingest",
    label: "RAG Ingest",
    description: "Ingest a document into the RAG knowledge base. The document will be chunked, embedded, and indexed for search.",
    parameters: Type.Object({
      source: Type.String({ description: "Document source (URL or file path)." }),
      content: Type.String({ description: "Document content." }),
      title: Type.Optional(Type.String({ description: "Document title." })),
      domain: Type.Optional(Type.String({ description: "Knowledge domain." })),
      tags: Type.Optional(Type.Array(Type.String(), { description: "Tags." })),
    }),
    async execute(_id, params) {
      const result = await getClient().ragIngest(params.source, params.content, params.title, params.domain, params.tags);
      return {
        content: [{ type: "text", text: `Ingested: ${params.source} (${result.chunk_count} chunks)` }],
        details: result,
      };
    },
  });

  pi.registerTool({
    name: "rag_search",
    label: "RAG Search",
    description: "Search RAG knowledge base using hybrid FTS5 + vector search.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query." }),
      domain: Type.Optional(Type.String({ description: "Filter by domain." })),
      limit: Type.Optional(Type.Number({ description: "Max results (default 5)." })),
    }),
    async execute(_id, params) {
      const result = await getClient().ragSearch(params.query, params.domain, params.limit);
      const formatted = result.results.map((r: any) => `[${r.heading || "No heading"}] ${r.content?.slice(0, 100)}`).join("\n");
      return {
        content: [{ type: "text", text: formatted || "No RAG results found." }],
        details: { count: result.count },
      };
    },
  });

  pi.registerTool({
    name: "rag_list",
    label: "RAG List",
    description: "List ingested RAG documents.",
    parameters: Type.Object({
      domain: Type.Optional(Type.String({ description: "Filter by domain." })),
      limit: Type.Optional(Type.Number({ description: "Max results (default 50)." })),
    }),
    async execute(_id, params) {
      const result = await getClient().ragList(params.domain, params.limit);
      const formatted = result.documents.map((d: any) => `[${d.id}] ${d.title || d.source}`).join("\n");
      return {
        content: [{ type: "text", text: `${result.count} documents:\n${formatted || "(empty)"}` }],
        details: { count: result.count },
      };
    },
  });

  pi.registerTool({
    name: "rag_delete",
    label: "RAG Delete",
    description: "Delete a RAG document and all its chunks.",
    parameters: Type.Object({
      document_id: Type.String({ description: "Document ID to delete." }),
    }),
    async execute(_id, params) {
      const result = await getClient().ragDelete(params.document_id);
      return {
        content: [{ type: "text", text: result.success ? `Deleted: ${params.document_id}` : "Delete failed" }],
        details: result,
      };
    },
  });

  // ── Learning Tool ─────────────────────────────────────

  pi.registerTool({
    name: "memory_learn",
    label: "Memory Learn",
    description: "Record a learning (solution, research, pattern, summary). Learning memories can be promoted to knowledge after sufficient use.",
    parameters: Type.Object({
      content: Type.String({ description: "The learning content." }),
      learned_type: Type.Optional(Type.String({ description: "Type: learned-research, learned-solution, learned-summary, learned-pattern." })),
      title: Type.Optional(Type.String({ description: "Short descriptive title." })),
      domain: Type.Optional(Type.String({ description: "Knowledge domain." })),
      tags: Type.Optional(Type.Array(Type.String(), { description: "Tags." })),
    }),
    async execute(_id, params) {
      const result = await getClient().learn(params.content, params.learned_type, params.title, params.domain, params.tags);
      return {
        content: [{ type: "text", text: `Learned: ${params.title || params.content.slice(0, 60)}` }],
        details: result,
      };
    },
  });

  pi.registerTool({
    name: "memory_unified_search",
    label: "Unified Search",
    description: "Unified search across structured memories, learned knowledge, and RAG documents.",
    parameters: Type.Object({
      query: Type.String({ description: "Search query." }),
      domain: Type.Optional(Type.String({ description: "Filter RAG by domain." })),
      limit: Type.Optional(Type.Number({ description: "Max results per category (default 5)." })),
    }),
    async execute(_id, params) {
      const result = await getClient().unifiedSearch(params.query, params.domain, params.limit);
      const parts = [];
      if (result.memories?.length) parts.push(`Memories: ${result.memories.length}`);
      if (result.learned?.length) parts.push(`Learned: ${result.learned.length}`);
      if (result.rag?.length) parts.push(`RAG: ${result.rag.length}`);
      return {
        content: [{ type: "text", text: `Found ${result.total} results (${parts.join(", ")})` }],
        details: result,
      };
    },
  });

  // ── Patrol Tools ──────────────────────────────────────

  pi.registerTool({
    name: "memory_patrol",
    label: "Memory Patrol",
    description: "Run a patrol: health check + optional self-learning.",
    parameters: Type.Object({
      include_learning: Type.Optional(Type.Boolean({ description: "Include self-learning phase (default: false)." })),
    }),
    async execute(_id, params) {
      const report = await getClient().patrol(params.include_learning ?? false);
      return {
        content: [{ type: "text", text: `Patrol: ${report.summary || "Complete"}` }],
        details: report,
      };
    },
  });

  pi.registerTool({
    name: "memory_patrol_log",
    label: "Patrol Log",
    description: "Get recent patrol log entries.",
    parameters: Type.Object({
      limit: Type.Optional(Type.Number({ description: "Max entries (default 20)." })),
    }),
    async execute(_id, params) {
      const result = await getClient().patrolLog(params.limit);
      return {
        content: [{ type: "text", text: `${result.logs.length} log entries:\n${result.logs.join("\n") || "(empty)"}` }],
        details: result,
      };
    },
  });

  // ── Slash Commands ────────────────────────────────────

  pi.registerCommand("memory", {
    description: "Memory operations: /memory stats | search <query> | list | save <content>",
    handler: async (args, ctx) => {
      const parts = args.trim().split(/\s+/);
      const sub = parts[0] || "stats";
      const rest = parts.slice(1).join(" ");

      try {
        if (sub === "stats") {
          const stats = await getClient().stats();
          const lines = [
            `Total: ${stats.total} | Pinned: ${stats.pinned} | Conflicts: ${stats.open_conflicts}`,
            `States: raw=${stats.by_state.raw} promoted=${stats.by_state.promoted} hot=${stats.by_state.hot} archived=${stats.by_state.archived}`,
            `Types: procedural=${stats.by_type.procedural} entity=${stats.by_type.entity} knowledge=${stats.by_type.knowledge}`,
          ];
          ctx.ui.notify(lines.join("\n"), "info");
        } else if (sub === "search" && rest) {
          const results = await getClient().hybrid(rest, { limit: 5 });
          if (results.length === 0) {
            ctx.ui.notify("No memories found.", "info");
          } else {
            ctx.ui.notify(results.map(formatEntry).join("\n\n"), "info");
          }
        } else if (sub === "list") {
          const result = await getClient().listEntries({ limit: 10 });
          ctx.ui.notify(`${result.total} entries:\n${result.entries.map(formatEntry).join("\n")}`, "info");
        } else if (sub === "save" && rest) {
          const entry = await getClient().save(rest);
          ctx.ui.notify(`Saved #${entry.id}: ${entry.title || entry.content.slice(0, 60)}`, "info");
        } else {
          ctx.ui.notify("Usage: /memory stats | search <query> | list | save <content>", "warning");
        }
      } catch (e: any) {
        ctx.ui.notify(`Memory error: ${e.message}`, "error");
      }
    },
  });

  pi.registerCommand("dream", {
    description: "Run memory dreaming (dry-run by default). Use /dream execute to apply.",
    handler: async (args, ctx) => {
      const dryRun = !args.trim().toLowerCase().includes("execute");
      try {
        const report = await getClient().dreaming(dryRun);
        const lines = [
          `Dreaming ${report.dry_run ? "(DRY RUN)" : "(EXECUTED)"}`,
          `Promote: ${report.candidates.promote} | Demote: ${report.candidates.demote} | Archive: ${report.candidates.archive}`,
        ];
        ctx.ui.notify(lines.join("\n"), "info");
      } catch (e: any) {
        ctx.ui.notify(`Dream error: ${e.message}`, "error");
      }
    },
  });

  pi.registerCommand("conflicts", {
    description: "View or resolve memory conflicts.",
    handler: async (args, ctx) => {
      try {
        const conflicts = await getClient().conflicts();
        if (conflicts.length === 0) {
          ctx.ui.notify("No open conflicts.", "info");
        } else {
          const formatted = conflicts.map((c) =>
            `#${c.id}: entries ${c.entry_a_id} ↔ ${c.entry_b_id} — ${c.reason}`
          ).join("\n");
          ctx.ui.notify(`Open conflicts:\n${formatted}`, "info");
        }
      } catch (e: any) {
        ctx.ui.notify(`Conflicts error: ${e.message}`, "error");
      }
    },
  });
}
