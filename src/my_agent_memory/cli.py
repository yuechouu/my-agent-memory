"""My Agent Memory CLI — human and agent command-line interface.

Usage:
  my-agent-memory search <query> [--limit N] [--tags t1,t2] [--scope shared|private] [--agent <id>]
  my-agent-memory save <content> [--title "..."] [--tags t1,t2] [--scope shared] [--project <name>]
  my-agent-memory get <id>
  my-agent-memory update <id> [--content "..."] [--title "..."] [--tags t1,t2]
  my-agent-memory pin <id>
  my-agent-memory unpin <id>
  my-agent-memory share <id>
  my-agent-memory unshare <id>
  my-agent-memory archive <id>
  my-agent-memory delete <id>
  my-agent-memory hybrid <query> [--limit N] [--scope shared]
  my-agent-memory status
  my-agent-memory dream [--execute] [--promote-threshold N] [--demote-threshold N]
  my-agent-memory consolidate --ids 1,2,3
  my-agent-memory conflicts [--resolve <id> --strategy merge|keep_both|last_write_wins]
  my-agent-memory rebuild
  my-agent-memory rebuild-hot
   my-agent-memory embed-pending [--limit N]
   my-agent-memory validate-pending [--limit N]
  my-agent-memory migrate --v1-db <path> --v1-hot <dir> --agent <id> [--v2-db <path>] [--execute]
  my-agent-memory serve [--port N]
  my-agent-memory system-prompt [--agent <id>] [--max-chars N]

For agents (import):
  from my_agent_memory import MultiAgentStore
  store = MultiAgentStore(agent_id="noor")
  store.search("query")
  store.save("fact", title="Title", tags=["tag"])
"""

import argparse
import json
import sys
import os

from my_agent_memory.store import MultiAgentStore


def _parse_tags(tag_str: str) -> list:
    return [t.strip() for t in tag_str.split(",") if t.strip()]


def _json_safe(obj):
    """Strip bytes fields for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    elif isinstance(obj, bytes):
        return None
    return obj


def _output(data, human: bool = False):
    if human and isinstance(data, list):
        for item in data:
            pin = "📌 " if item.get("is_pinned") else ""
            scope = f" [{item.get('scope', '')}]" if item.get("scope") != "private" else ""
            agent = f" ({item.get('owner_agent', '')})" if item.get("owner_agent") else ""
            mtype = f" {{{item.get('memory_type', '')}}}" if item.get("memory_type") else ""
            score = item.get("score", item.get("rrf_score", ""))
            if isinstance(score, float):
                score = f"{score:.3f}"
            print(f"[{item.get('id')}] {pin}{item.get('title', '(no title)')}{scope}{agent}{mtype}")
            print(f"    {item.get('content', '')[:120]}")
            print(f"    score: {score} | state: {item.get('state', '')} | access: {item.get('access_count', 0)}")
            print()
    elif human and isinstance(data, dict) and "total" in data:
        print(f"Total: {data['total']} | Pinned: {data.get('pinned', 0)} | Conflicts: {data.get('open_conflicts', 0)}")
        by_s = data.get("by_state", {})
        print(f"States: raw={by_s.get('raw',0)} hot={by_s.get('hot',0)} promoted={by_s.get('promoted',0)} archived={by_s.get('archived',0)}")
        by_scope = data.get("by_scope", {})
        print(f"Scopes: private={by_scope.get('private',0)} shared={by_scope.get('shared',0)} project={by_scope.get('project',0)}")
        by_type = data.get("by_type", {})
        if by_type:
            print(f"Types: procedural={by_type.get('procedural',0)} entity={by_type.get('entity',0)} knowledge={by_type.get('knowledge',0)}")
        if data.get("by_agent"):
            print("By agent:", json.dumps(data["by_agent"], ensure_ascii=False))
        print(f"DB: {data.get('db_path', '')}")
        if data.get("last_dreaming"):
            print(f"Last dreaming: {data['last_dreaming']}")
    elif human and isinstance(data, dict):
        print(json.dumps(_json_safe(data), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(_json_safe(data), ensure_ascii=False))


def _get_store(db_path: str = "", agent_id: str = "") -> MultiAgentStore:
    agent_id = agent_id or os.getenv("HERMES_AGENT_ID", "")
    return MultiAgentStore(agent_id=agent_id, db_path=db_path)


# ── Command handlers ────────────────────────────────────────

def _get_store_from_args(args) -> MultiAgentStore:
    db_path = getattr(args, 'db_path', '') or ""
    agent_id = getattr(args, 'agent', '') or ""
    return _get_store(db_path, agent_id)


def _cmd_search(args):
    s = _get_store_from_args(args)
    tags = _parse_tags(args.tags) if args.tags else None
    mt = getattr(args, 'memory_type', '') or None
    _output(s.search(args.query, args.limit, args.offset, tags, scope=args.scope, agent_id=args.agent, memory_type=mt),
            human=getattr(args, "human", False))


def _cmd_save(args):
    s = _get_store_from_args(args)
    tags = _parse_tags(args.tags) if args.tags else None
    mt = getattr(args, 'memory_type', '') or None
    _output(s.save(args.content, args.title, tags, args.source or "manual", args.scope or "private", args.project, memory_type=mt),
            human=True)


def _cmd_get(args):
    s = _get_store_from_args(args)
    result = s.get(args.id)
    _output(result or {"error": f"Entry {args.id} not found"}, human=True)


def _cmd_update(args):
    s = _get_store_from_args(args)
    fields = {}
    if args.content is not None: fields["content"] = args.content
    if args.title is not None: fields["title"] = args.title
    if args.tags: fields["tags"] = _parse_tags(args.tags)
    if args.scope: fields["scope"] = args.scope
    if args.project: fields["project"] = args.project
    result = s.update(args.id, **fields)
    _output(result or {"error": f"Entry {args.id} not found"}, human=True)


def _cmd_pin(args): _output(_get_store_from_args(args).pin(args.id), human=True)
def _cmd_unpin(args): _output(_get_store_from_args(args).unpin(args.id), human=True)
def _cmd_share(args): _output(_get_store_from_args(args).share(args.id), human=True)
def _cmd_unshare(args): _output(_get_store_from_args(args).unshare(args.id), human=True)
def _cmd_archive_cmd(args): _output(_get_store_from_args(args).archive(args.id), human=True)


def _cmd_delete(args):
    s = _get_store_from_args(args)
    success = s.delete(args.id)
    _output({"ok": success, "id": args.id}, human=True)


def _cmd_hybrid(args):
    s = _get_store_from_args(args)
    mt = getattr(args, 'memory_type', '') or None
    rerank = getattr(args, 'rerank', False)
    _output(s.hybrid_search(args.query, limit=args.limit, scope=args.scope, agent_id=args.agent, memory_type=mt, rerank=rerank),
            human=getattr(args, "human", False))


def _cmd_status(args):
    _output(_get_store_from_args(args).stats(), human=True)


def _cmd_dream(args):
    s = _get_store_from_args(args)
    _output(s.dreaming(
        dry_run=not args.execute,
        promote_threshold=args.promote_threshold,
        demote_threshold=args.demote_threshold,
        archive_threshold=args.archive_threshold,
    ), human=True)


def _cmd_consolidate(args):
    s = _get_store_from_args(args)
    entry_ids = [int(x) for x in args.ids.split(",") if x.strip()]
    result = s.consolidate(entry_ids)
    _output(result or {"error": "No entries to consolidate"}, human=True)


def _cmd_conflicts(args):
    s = _get_store_from_args(args)
    if args.resolve:
        result = s.resolve_conflict(args.resolve, args.strategy or "dismiss")
        _output(result or {"error": f"Conflict {args.resolve} not found"}, human=True)
    else:
        _output(s.get_conflicts("open"), human=True)


def _cmd_rebuild(args): _get_store_from_args(args).rebuild(); _output({"ok": True, "message": "FTS5 index rebuilt"})


def _cmd_rebuild_hot(args):
    _get_store_from_args(args).rebuild_hot_layer()
    _output({"ok": True, "message": "Hot layer rebuilt"})


def _cmd_embed_pending(args):
    s = _get_store_from_args(args)
    count = s.embed_pending(limit=args.limit)
    _output({"ok": True, "embedded": count}, human=True)

def _cmd_validate_pending(args):
    s = _get_store_from_args(args)
    count = s.validate_pending(limit=args.limit)
    _output({"ok": True, "validated": count}, human=True)


def _cmd_migrate(args):
    from my_agent_memory.migrate import migrate_from_v1
    result = migrate_from_v1(
        v1_db_path=args.v1_db,
        v1_hot_dir=args.v1_hot,
        agent_id=args.agent or "noor",
        v2_db_path=args.v2_db or "",
        dry_run=not args.execute,
    )
    _output(result, human=True)


def _cmd_serve(args):
    from my_agent_memory.serve import run_server
    run_server(
        port=args.port or 8765,
        store_factory=_get_store,
        dream_interval=getattr(args, 'dream_interval', 0) or 0,
    )


def _cmd_system_prompt(args):
    s = _get_store_from_args(args)
    block = s.get_system_prompt_block(
        agent_id=args.agent or s.agent_id,
        max_chars=args.max_chars,
    )
    print(block)


def _cmd_mcp_server(args):
    from my_agent_memory.mcp_server import run_mcp_server
    import asyncio
    db_path = args.db_path or getattr(args, 'db_path', '') or ""
    agent_id = args.agent_id or "claude-code"
    asyncio.run(run_mcp_server(db_path=db_path, agent_id=agent_id))


# ── Parser ──────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="my-agent-memory",
        description="My Agent Memory — multi-agent memory system",
    )
    parser.add_argument("--db-path", default="", help="Path to memory_v2.db")
    parser.add_argument("--agent", default="", help="Agent ID (default: $HERMES_AGENT_ID)")
    sub = parser.add_subparsers(dest="command")

    # search
    p = sub.add_parser("search", help="FTS5 full-text search")
    p.add_argument("query"); p.add_argument("--limit", type=int, default=10)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--tags"); p.add_argument("--scope"); p.add_argument("--agent")
    p.add_argument("--type", dest="memory_type", default="")
    p.add_argument("--human", action="store_true")
    p.set_defaults(handler=_cmd_search)

    # save
    p = sub.add_parser("save", help="Save a new memory entry")
    p.add_argument("content"); p.add_argument("--title", default="")
    p.add_argument("--tags"); p.add_argument("--source", default="manual")
    p.add_argument("--scope", default="private"); p.add_argument("--project")
    p.add_argument("--type", dest="memory_type", default="",
                   choices=["procedural", "entity", "knowledge", ""],
                   help="Memory type (auto-detected if omitted)")
    p.set_defaults(handler=_cmd_save)

    # get
    p = sub.add_parser("get", help="Get entry by ID")
    p.add_argument("id", type=int); p.add_argument("--human", action="store_true")
    p.set_defaults(handler=_cmd_get)

    # update
    p = sub.add_parser("update", help="Update entry fields")
    p.add_argument("id", type=int); p.add_argument("--content"); p.add_argument("--title")
    p.add_argument("--tags"); p.add_argument("--scope"); p.add_argument("--project")
    p.set_defaults(handler=_cmd_update)

    # pin / unpin
    p = sub.add_parser("pin", help="Pin entry (exempt from demote/archive)"); p.add_argument("id", type=int); p.set_defaults(handler=_cmd_pin)
    p = sub.add_parser("unpin", help="Unpin entry"); p.add_argument("id", type=int); p.set_defaults(handler=_cmd_unpin)

    # share / unshare
    p = sub.add_parser("share", help="Share entry (private → shared)"); p.add_argument("id", type=int); p.set_defaults(handler=_cmd_share)
    p = sub.add_parser("unshare", help="Unshare entry (shared → private)"); p.add_argument("id", type=int); p.set_defaults(handler=_cmd_unshare)

    # archive / delete
    p = sub.add_parser("archive", help="Archive entry (soft delete)"); p.add_argument("id", type=int); p.set_defaults(handler=_cmd_archive_cmd)
    p = sub.add_parser("delete", help="Hard delete entry"); p.add_argument("id", type=int); p.set_defaults(handler=_cmd_delete)

    # hybrid
    p = sub.add_parser("hybrid", help="Hybrid search (FTS5 + vector + RRF)")
    p.add_argument("query"); p.add_argument("--limit", type=int, default=10)
    p.add_argument("--scope"); p.add_argument("--agent")
    p.add_argument("--type", dest="memory_type", default="")
    p.add_argument("--rerank", action="store_true", help="Apply semantic reranking")
    p.add_argument("--human", action="store_true")
    p.set_defaults(handler=_cmd_hybrid)

    # status
    sub.add_parser("status", help="Memory statistics").set_defaults(handler=_cmd_status)

    # dream
    p = sub.add_parser("dream", help="Run dreaming lifecycle pass")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--promote-threshold", type=float)
    p.add_argument("--demote-threshold", type=float)
    p.add_argument("--archive-threshold", type=float)
    p.set_defaults(handler=_cmd_dream)

    # consolidate
    p = sub.add_parser("consolidate", help="Merge entries"); p.add_argument("--ids", default=""); p.set_defaults(handler=_cmd_consolidate)

    # conflicts
    p = sub.add_parser("conflicts", help="View/resolve conflicts")
    p.add_argument("--resolve", type=int); p.add_argument("--strategy"); p.add_argument("--merged-content")
    p.set_defaults(handler=_cmd_conflicts)

    # rebuild
    sub.add_parser("rebuild", help="Rebuild FTS5 index").set_defaults(handler=_cmd_rebuild)
    sub.add_parser("rebuild-hot", help="Rebuild hot layer markdown files").set_defaults(handler=_cmd_rebuild_hot)

    # embed-pending
    p = sub.add_parser("embed-pending", help="Generate embeddings for entries without them")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(handler=_cmd_embed_pending)

    # validate-pending
    p = sub.add_parser("validate-pending", help="Run async LLM validation on unchecked entries")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(handler=_cmd_validate_pending)

    # migrate
    p = sub.add_parser("migrate", help="Migrate from v1 to v2")
    p.add_argument("--v1-db", required=True); p.add_argument("--v1-hot", required=True)
    p.add_argument("--agent", default="noor"); p.add_argument("--v2-db", default="")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(handler=_cmd_migrate)

    # serve
    p = sub.add_parser("serve", help="Start web management dashboard")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dream-interval", type=int, default=0, help="Auto-dream interval in minutes (0=disabled)")
    p.set_defaults(handler=_cmd_serve)

    # mcp-server
    p = sub.add_parser("mcp-server", help="Run MCP server for Claude Code integration")
    p.add_argument("--db-path", default="", help="SQLite database path")
    p.add_argument("--agent-id", default="claude-code", help="Agent identifier")
    p.set_defaults(handler=_cmd_mcp_server)

    # system-prompt
    p = sub.add_parser("system-prompt", help="Print hot layer for system prompt injection")
    p.add_argument("--agent"); p.add_argument("--max-chars", type=int)
    p.set_defaults(handler=_cmd_system_prompt)

    # RAG commands
    p = sub.add_parser("rag", help="RAG document operations")
    rag_sub = p.add_subparsers(dest="rag_command")

    p_ingest = rag_sub.add_parser("ingest", help="Ingest a document")
    p_ingest.add_argument("source", help="Document source (URL or file path)")
    p_ingest.add_argument("--title", help="Document title")
    p_ingest.add_argument("--domain", help="Knowledge domain")
    p_ingest.add_argument("--tags", default="", help="Comma-separated tags")
    p_ingest.add_argument("--file", help="Read content from file instead of stdin")
    p_ingest.set_defaults(handler=_cmd_rag_ingest)

    p_search = rag_sub.add_parser("search", help="Search RAG documents")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--domain", help="Filter by domain")
    p_search.add_argument("--limit", type=int, default=5, help="Max results")
    p_search.set_defaults(handler=_cmd_rag_search)

    p_list = rag_sub.add_parser("list", help="List RAG documents")
    p_list.add_argument("--domain", help="Filter by domain")
    p_list.add_argument("--limit", type=int, default=50, help="Max results")
    p_list.set_defaults(handler=_cmd_rag_list)

    p_delete = rag_sub.add_parser("delete", help="Delete RAG document")
    p_delete.add_argument("document_id", help="Document ID to delete")
    p_delete.set_defaults(handler=_cmd_rag_delete)

    p_dir = rag_sub.add_parser("ingest-dir", help="Batch import from directory")
    p_dir.add_argument("path", help="Directory path")
    p_dir.add_argument("--domain", help="Knowledge domain")
    p_dir.add_argument("--tags", default="", help="Comma-separated tags")
    p_dir.add_argument("--pattern", default="*.md,*.txt,*.rst", help="File patterns (comma-separated)")
    p_dir.add_argument("--ignore", default="node_modules,.git,__pycache__,.venv", help="Ignore patterns")
    p_dir.set_defaults(handler=_cmd_rag_ingest_dir)

    p_url = rag_sub.add_parser("ingest-url", help="Fetch and ingest from URL")
    p_url.add_argument("url", help="URL to fetch")
    p_url.add_argument("--domain", help="Knowledge domain")
    p_url.add_argument("--tags", default="", help="Comma-separated tags")
    p_url.set_defaults(handler=_cmd_rag_ingest_url)

    p_git = rag_sub.add_parser("ingest-git", help="Import from git repository")
    p_git.add_argument("repo", help="Git repository URL or local path")
    p_git.add_argument("--path", default="", help="Subdirectory to import")
    p_git.add_argument("--domain", help="Knowledge domain")
    p_git.add_argument("--tags", default="", help="Comma-separated tags")
    p_git.add_argument("--pattern", default="*.md,*.txt,*.rst", help="File patterns")
    p_git.set_defaults(handler=_cmd_rag_ingest_git)

    p_sync = rag_sub.add_parser("sync", help="Sync RAG with source files")
    p_sync.add_argument("--remove", action="store_true", help="Remove entries for missing sources")
    p_sync.set_defaults(handler=_cmd_rag_sync)

    p_cleanup = rag_sub.add_parser("cleanup", help="Remove RAG entries for missing sources")
    p_cleanup.set_defaults(handler=_cmd_rag_cleanup)

    # Learn commands
    p = sub.add_parser("learn", help="Record a learning")
    p.add_argument("content", help="Learning content")
    p.add_argument("--type", dest="learned_type", default="learned-solution",
                   choices=["learned-research", "learned-solution", "learned-summary", "learned-pattern"],
                   help="Type of learning")
    p.add_argument("--title", help="Short title")
    p.add_argument("--domain", help="Knowledge domain")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.set_defaults(handler=_cmd_learn)

    # Unified search
    p = sub.add_parser("unified", help="Unified search (memories + learned + RAG)")
    p.add_argument("query", help="Search query")
    p.add_argument("--domain", help="Filter RAG by domain")
    p.add_argument("--limit", type=int, default=5, help="Max results per category")
    p.set_defaults(handler=_cmd_unified_search)

    # Patrol commands
    p = sub.add_parser("patrol", help="Run patrol (health check + learning)")
    p.add_argument("--learn", action="store_true", help="Include self-learning phase")
    p.add_argument("--log", action="store_true", help="Show patrol log")
    p.add_argument("--activity", action="store_true", help="Show activity files")
    p.set_defaults(handler=_cmd_patrol)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if hasattr(args, "handler"):
        args.handler(args)
    else:
        parser.print_help()


# ── RAG command handlers ─────────────────────────────────────

def _cmd_rag_ingest(args):
    """Ingest a document into RAG."""
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    # Read content
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            content = f.read()
    elif not sys.stdin.isatty():
        content = sys.stdin.read()
    else:
        print("Error: Provide content via --file or stdin", file=sys.stderr)
        sys.exit(1)

    tags = _parse_tags(args.tags) if args.tags else []
    result = rag.ingest(
        source=args.source,
        content=content,
        title=args.title,
        domain=args.domain,
        tags=tags,
    )
    _output(result, human=True)


def _cmd_rag_search(args):
    """Search RAG documents."""
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    results = rag.search(
        query=args.query,
        domain=args.domain,
        limit=args.limit,
    )
    _output({"results": results, "count": len(results)}, human=True)


def _cmd_rag_list(args):
    """List RAG documents."""
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    results = rag.list_documents(domain=args.domain, limit=args.limit)
    _output({"documents": results, "count": len(results)}, human=True)


def _cmd_rag_delete(args):
    """Delete a RAG document."""
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    success = rag.delete(args.document_id)
    _output({"success": success}, human=True)


def _cmd_rag_ingest_dir(args):
    """Batch import documents from a directory."""
    import fnmatch
    from pathlib import Path
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    dir_path = Path(args.path)
    if not dir_path.is_dir():
        print(f"Error: {args.path} is not a directory", file=sys.stderr)
        sys.exit(1)

    patterns = [p.strip() for p in args.pattern.split(",")]
    ignore = [p.strip() for p in args.ignore.split(",")]
    tags = _parse_tags(args.tags) if args.tags else []

    results = []
    for file_path in sorted(dir_path.rglob("*")):
        if not file_path.is_file():
            continue

        # Check ignore patterns
        rel_path = file_path.relative_to(dir_path)
        parts = rel_path.parts
        if any(ig in parts for ig in ignore):
            continue

        # Check file patterns
        if not any(fnmatch.fnmatch(file_path.name, p) for p in patterns):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
            if len(content) < 50:  # Skip very small files
                continue

            source = str(rel_path)
            result = rag.ingest(
                source=source,
                content=content,
                title=file_path.stem,
                domain=args.domain,
                tags=tags,
            )
            results.append({"file": source, **result})
            print(f"  ✓ {source} ({result['chunk_count']} chunks)")
        except Exception as e:
            print(f"  ✗ {file_path}: {e}", file=sys.stderr)

    _output({"imported": len(results), "files": results}, human=True)


def _cmd_rag_ingest_url(args):
    """Fetch and ingest content from a URL."""
    import urllib.request
    import re
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    tags = _parse_tags(args.tags) if args.tags else []

    try:
        print(f"Fetching {args.url}...")
        req = urllib.request.Request(args.url, headers={"User-Agent": "my-agent-memory/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="ignore")

        # Simple HTML to text conversion
        if "<html" in content.lower()[:500]:
            # Remove HTML tags
            content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<[^>]+>", "", content)
            content = re.sub(r"\s+", " ", content)
            content = content.strip()

        if len(content) < 50:
            print("Error: Content too short", file=sys.stderr)
            sys.exit(1)

        result = rag.ingest(
            source=args.url,
            content=content,
            domain=args.domain,
            tags=tags,
        )
        _output({"status": "ingested", **result}, human=True)
    except Exception as e:
        print(f"Error fetching URL: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_rag_ingest_git(args):
    """Import documents from a git repository."""
    import subprocess
    import tempfile
    from pathlib import Path
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    tags = _parse_tags(args.tags) if args.tags else []

    # Determine if local or remote
    repo_path = Path(args.repo)
    is_local = repo_path.exists()

    if is_local:
        work_dir = repo_path
    else:
        # Clone to temp directory
        work_dir = Path(tempfile.mkdtemp(prefix="rag-git-"))
        print(f"Cloning {args.repo}...")
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", args.repo, str(work_dir)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"Error cloning: {e.stderr}", file=sys.stderr)
            sys.exit(1)

    # Import using ingest-dir
    target_dir = work_dir / args.path if args.path else work_dir
    if not target_dir.is_dir():
        print(f"Error: {args.path} not found in repository", file=sys.stderr)
        sys.exit(1)

    # Reuse ingest-dir logic
    args.path = str(target_dir)
    _cmd_rag_ingest_dir(args)

    # Cleanup temp directory
    if not not is_local:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)


def _cmd_rag_sync(args):
    """Sync RAG documents with source files."""
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    print("Syncing RAG documents with sources...")
    result = rag.sync(remove_orphans=args.remove)

    print(f"\nTotal: {result['total']}")
    print(f"Valid: {result['valid']}")
    print(f"Updated: {result['updated']}")
    print(f"Missing: {result['missing']}")

    if result['removed']:
        print(f"Removed: {result['removed']}")

    if result['missing_docs']:
        print("\nMissing sources:")
        for doc in result['missing_docs']:
            print(f"  - {doc['source']} (id: {doc['id']})")

    if result['missing'] and not args.remove:
        print("\nTip: Use --remove to delete entries for missing sources")


def _cmd_rag_cleanup(args):
    """Remove RAG entries for missing sources."""
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    print("Cleaning up RAG entries for missing sources...")
    result = rag.cleanup()

    print(f"\nTotal: {result['total']}")
    print(f"Valid: {result['valid']}")
    print(f"Removed: {result['removed']}")

    if result['missing_docs']:
        print("\nRemoved:")
        for doc in result['missing_docs']:
            print(f"  - {doc['source']}")


# ── Learn command handlers ─────────────────────────────────

def _cmd_learn(args):
    """Record a learning."""
    store = _get_store_from_args(args)
    tags = _parse_tags(args.tags) if args.tags else []
    if args.domain:
        tags.append(f"domain:{args.domain}")

    entry = store.save(
        content=args.content,
        title=args.title or "",
        tags=tags,
        scope="private",
        memory_type=args.learned_type,
    )
    entry.pop("embedding", None)
    _output({"status": "learned", "entry": entry}, human=True)


# ── Unified search handler ──────────────────────────────────

def _cmd_unified_search(args):
    """Unified search across memories, learned, and RAG."""
    store = _get_store_from_args(args)

    result = store.unified_search(
        query=args.query,
        domain=args.domain,
        limit=args.limit,
    )

    if sys.stdout.isatty():
        print(f"=== Memories ({len(result['memories'])}) ===")
        for m in result["memories"]:
            print(f"  [{m.get('id')}] {m.get('title', '(no title)')}")
            print(f"    {m.get('content', '')[:100]}")
            print()

        print(f"=== Learned ({len(result['learned'])}) ===")
        for l in result["learned"]:
            print(f"  [{l.get('id')}] {l.get('title', '(no title)')}")
            print(f"    {l.get('content', '')[:100]}")
            print()

        print(f"=== RAG ({len(result['rag'])}) ===")
        for r in result["rag"]:
            print(f"  [{r.get('id')}] {r.get('heading', '(no heading)')}")
            print(f"    {r.get('content', '')[:100]}")
            print()

        print(f"Total: {result['total']}")
    else:
        _output(result)


# ── Patrol command handlers ──────────────────────────────────

def _cmd_patrol(args):
    """Run patrol (health check + optional learning)."""
    from my_agent_memory.patrol import PatrolEngine
    from my_agent_memory.rag import RAGEngine

    store = _get_store_from_args(args)
    rag = RAGEngine(db=store.db, embed_client=store.embed_client)

    patrol = PatrolEngine(store=store, rag_engine=rag)

    if args.log:
        # 显示巡检日志
        logs = patrol.get_patrol_log(limit=20)
        if logs:
            print("=== 巡检日志 ===")
            for log in logs:
                print(log)
        else:
            print("暂无巡检日志")
        return

    if args.activity:
        # 显示活动文件
        files = patrol.get_activity_files()
        if files:
            print("=== 活动文件 ===")
            for f in files:
                print(f"  {f}")
        else:
            print("暂无活动文件")
        return

    # 执行巡检
    print("执行巡检...")
    report = patrol.patrol(include_learning=args.learn)

    # 输出结果
    print(f"\n=== 巡检报告 ===")
    print(f"摘要: {report.get('summary', '无')}")

    # Phase 1
    p1 = report.get("phase1", {})
    mh = p1.get("memory_health", {})
    print(f"\n📊 记忆健康:")
    print(f"  总数: {mh.get('total', 0)}")
    print(f"  过期: {len(mh.get('stale_memories', []))}")
    print(f"  冲突: {len(mh.get('conflicts', []))}")
    print(f"  低质量: {len(mh.get('low_quality', []))}")

    rh = p1.get("rag_health", {})
    if rh.get("total_documents"):
        print(f"\n📚 RAG 健康:")
        print(f"  文档: {rh.get('total_documents', 0)}")
        print(f"  有效: {rh.get('valid', 0)}")
        print(f"  缺失: {rh.get('missing', 0)}")

    promotions = p1.get("promotions", [])
    if promotions:
        print(f"\n⬆️ 晋升:")
        for p in promotions:
            print(f"  {p['from']} → {p['to']} (score: {p['score']:.2f})")

    # Phase 2
    if args.learn:
        p2 = report.get("phase2", {})
        learnings = p2.get("learnings", [])
        if learnings:
            print(f"\n📖 学习:")
            for l in learnings:
                print(f"  {l['topic']}")

    # Actions
    actions = report.get("actions", [])
    if actions:
        print(f"\n💡 行动:")
        for a in actions:
            print(f"  - {a}")

    print(f"\n日志: {patrol.patrol_log_path}")


if __name__ == "__main__":
    main()
