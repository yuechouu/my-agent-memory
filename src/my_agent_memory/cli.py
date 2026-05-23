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
            score = item.get("score", item.get("rrf_score", ""))
            if isinstance(score, float):
                score = f"{score:.3f}"
            print(f"[{item.get('id')}] {pin}{item.get('title', '(no title)')}{scope}{agent}")
            print(f"    {item.get('content', '')[:120]}")
            print(f"    score: {score} | state: {item.get('state', '')} | access: {item.get('access_count', 0)}")
            print()
    elif human and isinstance(data, dict) and "total" in data:
        print(f"Total: {data['total']} | Pinned: {data.get('pinned', 0)} | Conflicts: {data.get('open_conflicts', 0)}")
        by_s = data.get("by_state", {})
        print(f"States: raw={by_s.get('raw',0)} hot={by_s.get('hot',0)} promoted={by_s.get('promoted',0)} archived={by_s.get('archived',0)}")
        by_scope = data.get("by_scope", {})
        print(f"Scopes: private={by_scope.get('private',0)} shared={by_scope.get('shared',0)} project={by_scope.get('project',0)}")
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
    _output(s.search(args.query, args.limit, args.offset, tags, scope=args.scope, agent_id=args.agent),
            human=getattr(args, "human", False))


def _cmd_save(args):
    s = _get_store_from_args(args)
    tags = _parse_tags(args.tags) if args.tags else None
    _output(s.save(args.content, args.title, tags, args.source or "manual", args.scope or "private", args.project),
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
    _output(s.hybrid_search(args.query, limit=args.limit, scope=args.scope, agent_id=args.agent),
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
    run_server(port=args.port or 8765, store_factory=_get_store)


def _cmd_system_prompt(args):
    s = _get_store_from_args(args)
    block = s.get_system_prompt_block(
        agent_id=args.agent or s.agent_id,
        max_chars=args.max_chars,
    )
    print(block)


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
    p.add_argument("--human", action="store_true")
    p.set_defaults(handler=_cmd_search)

    # save
    p = sub.add_parser("save", help="Save a new memory entry")
    p.add_argument("content"); p.add_argument("--title", default="")
    p.add_argument("--tags"); p.add_argument("--source", default="manual")
    p.add_argument("--scope", default="private"); p.add_argument("--project")
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
    p.add_argument("--scope"); p.add_argument("--agent"); p.add_argument("--human", action="store_true")
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
    p.set_defaults(handler=_cmd_serve)

    # system-prompt
    p = sub.add_parser("system-prompt", help="Print hot layer for system prompt injection")
    p.add_argument("--agent"); p.add_argument("--max-chars", type=int)
    p.set_defaults(handler=_cmd_system_prompt)

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


if __name__ == "__main__":
    main()
