"""MCP server for my-agent-memory — exposes memory tools to Claude Code.

Usage:
  my-agent-memory mcp-server [--db-path PATH] [--agent-id ID]

Configure in Claude Code settings.json:
{
  "mcpServers": {
    "my-agent-memory": {
      "command": "my-agent-memory",
      "args": ["mcp-server"]
    }
  }
}
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from my_agent_memory.store import MultiAgentStore
from my_agent_memory.rag import RAGEngine


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    elif isinstance(obj, bytes):
        return None
    return obj


def create_server(db_path: str = "", agent_id: str = "claude-code") -> Server:
    """Create MCP server instance with memory tools."""
    store = MultiAgentStore(db_path=db_path, agent_id=agent_id)
    rag = RAGEngine(db=store.db, embed_client=store.embed if hasattr(store, 'embed') else None)
    server = Server("my-agent-memory")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="memory_search",
                description=(
                    "Search persistent memory using hybrid FTS5 + vector search. "
                    "Use when: user asks about past interactions, preferences, project details."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."},
                        "memory_type": {"type": "string", "enum": ["procedural", "entity", "knowledge"], "description": "Filter by type."},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_save",
                description=(
                    "Save a durable fact to persistent memory. "
                    "Use when: user shares important info, says 'remember this', or you learn durable facts."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The fact to remember."},
                        "title": {"type": "string", "description": "Short descriptive title."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization."},
                        "scope": {"type": "string", "enum": ["private", "shared"], "description": "Visibility (default: private)."},
                        "memory_type": {"type": "string", "enum": ["procedural", "entity", "knowledge"], "description": "Memory type. Auto-detected if omitted."},
                    },
                    "required": ["content"],
                },
            ),
            Tool(
                name="memory_update",
                description="Update the content, title, or tags of an existing memory entry.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "integer", "description": "Memory entry ID."},
                        "content": {"type": "string", "description": "New content."},
                        "title": {"type": "string", "description": "New title."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "New tags."},
                    },
                    "required": ["entry_id"],
                },
            ),
            Tool(
                name="memory_pin",
                description="Pin a memory so it's never auto-archived and always in the hot layer.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "integer", "description": "Memory entry ID."},
                        "unpin": {"type": "boolean", "description": "Set true to unpin."},
                    },
                    "required": ["entry_id"],
                },
            ),
            Tool(
                name="memory_archive",
                description="Archive (soft-delete) a memory entry that is no longer relevant.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "integer", "description": "Memory entry ID."},
                    },
                    "required": ["entry_id"],
                },
            ),
            Tool(
                name="memory_list",
                description="List recent memory entries with optional filters.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "enum": ["raw", "promoted", "hot", "archived"], "description": "Filter by state."},
                        "memory_type": {"type": "string", "enum": ["procedural", "entity", "knowledge"], "description": "Filter by type."},
                        "limit": {"type": "integer", "description": "Max results (default 10)."},
                    },
                },
            ),
            Tool(
                name="memory_dream",
                description="Run a dreaming lifecycle pass. Promotes popular memories, demotes stale ones.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dry_run": {"type": "boolean", "description": "Preview only (default true)."},
                    },
                },
            ),
            Tool(
                name="memory_stats",
                description="Show memory database statistics (total, by state, by type, etc).",
                inputSchema={"type": "object", "properties": {}},
            ),
            # RAG tools
            Tool(
                name="rag_ingest",
                description=(
                    "Ingest a document into the RAG knowledge base. "
                    "The document will be chunked, embedded, and indexed for search."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Document source (URL or file path)."},
                        "content": {"type": "string", "description": "Document content (markdown, text, etc.)."},
                        "title": {"type": "string", "description": "Document title."},
                        "domain": {"type": "string", "description": "Knowledge domain (programming, math, etc.)."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization."},
                    },
                    "required": ["source", "content"],
                },
            ),
            Tool(
                name="rag_search",
                description=(
                    "Search RAG knowledge base using hybrid FTS5 + vector search. "
                    "Use when: user asks about technical topics, API docs, code patterns."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "domain": {"type": "string", "description": "Filter by domain."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="rag_list",
                description="List ingested RAG documents.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Filter by domain."},
                        "limit": {"type": "integer", "description": "Max results (default 50)."},
                    },
                },
            ),
            Tool(
                name="rag_delete",
                description="Delete a RAG document and all its chunks.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_id": {"type": "string", "description": "Document ID to delete."},
                    },
                    "required": ["document_id"],
                },
            ),
            Tool(
                name="rag_ingest_dir",
                description="Batch import documents from a directory. Supports file pattern matching.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path."},
                        "domain": {"type": "string", "description": "Knowledge domain."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags."},
                        "pattern": {"type": "string", "description": "File patterns (default: *.md,*.txt,*.rst)."},
                        "ignore": {"type": "string", "description": "Ignore patterns (default: node_modules,.git)."},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="rag_ingest_url",
                description="Fetch and ingest content from a URL.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch."},
                        "domain": {"type": "string", "description": "Knowledge domain."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags."},
                    },
                    "required": ["url"],
                },
            ),
            # Learning tools
            Tool(
                name="memory_learn",
                description=(
                    "Record a learning (solution, research, pattern, summary). "
                    "Learning memories can be promoted to knowledge after sufficient use."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The learning content."},
                        "learned_type": {
                            "type": "string",
                            "enum": ["learned-research", "learned-solution", "learned-summary", "learned-pattern"],
                            "description": "Type of learning (default: learned-solution).",
                        },
                        "title": {"type": "string", "description": "Short descriptive title."},
                        "domain": {"type": "string", "description": "Knowledge domain."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags."},
                    },
                    "required": ["content"],
                },
            ),
            Tool(
                name="memory_unified_search",
                description=(
                    "Unified search across structured memories, learned knowledge, and RAG documents. "
                    "Best for comprehensive searches."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "domain": {"type": "string", "description": "Filter RAG by domain."},
                        "limit": {"type": "integer", "description": "Max results per category (default 5)."},
                    },
                    "required": ["query"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "memory_search":
                results = store.hybrid_search(
                    arguments["query"],
                    limit=int(arguments.get("limit", 5)),
                    memory_type=arguments.get("memory_type"),
                )
                for r in results:
                    r.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe({"results": results, "count": len(results)}), ensure_ascii=False))]

            elif name == "memory_save":
                tags = arguments.get("tags", [])
                entry = store.save(
                    content=arguments["content"],
                    title=arguments.get("title", ""),
                    tags=tags,
                    scope=arguments.get("scope", "private"),
                    memory_type=arguments.get("memory_type"),
                )
                entry.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe({"status": "saved", "entry": entry}), ensure_ascii=False))]

            elif name == "memory_update":
                fields = {}
                if arguments.get("content"):
                    fields["content"] = arguments["content"]
                if arguments.get("title"):
                    fields["title"] = arguments["title"]
                if arguments.get("tags"):
                    fields["tags"] = arguments["tags"]
                result = store.update(int(arguments["entry_id"]), **fields)
                if result:
                    result.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe({"status": "updated", "entry": result}), ensure_ascii=False))]

            elif name == "memory_pin":
                entry_id = int(arguments["entry_id"])
                if arguments.get("unpin"):
                    result = store.unpin(entry_id)
                else:
                    result = store.pin(entry_id)
                if result:
                    result.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe({"status": "ok", "entry": result}), ensure_ascii=False))]

            elif name == "memory_archive":
                result = store.archive(int(arguments["entry_id"]))
                if result:
                    result.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe({"status": "archived", "entry": result}), ensure_ascii=False))]

            elif name == "memory_list":
                result = store.list_entries(
                    state=arguments.get("state"),
                    memory_type=arguments.get("memory_type"),
                    limit=int(arguments.get("limit", 10)),
                )
                for r in result.get("entries", []):
                    r.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe(result), ensure_ascii=False))]

            elif name == "memory_dream":
                report = store.dreaming(dry_run=arguments.get("dry_run", True))
                return [TextContent(type="text", text=json.dumps(_json_safe(report), ensure_ascii=False))]

            elif name == "memory_stats":
                stats = store.stats()
                return [TextContent(type="text", text=json.dumps(_json_safe(stats), ensure_ascii=False))]

            # RAG tools
            elif name == "rag_ingest":
                result = rag.ingest(
                    source=arguments["source"],
                    content=arguments["content"],
                    title=arguments.get("title"),
                    domain=arguments.get("domain"),
                    tags=arguments.get("tags"),
                )
                return [TextContent(type="text", text=json.dumps(_json_safe(result), ensure_ascii=False))]

            elif name == "rag_search":
                results = rag.search(
                    query=arguments["query"],
                    domain=arguments.get("domain"),
                    limit=int(arguments.get("limit", 5)),
                )
                return [TextContent(type="text", text=json.dumps(_json_safe({"results": results, "count": len(results)}), ensure_ascii=False))]

            elif name == "rag_list":
                results = rag.list_documents(
                    domain=arguments.get("domain"),
                    limit=int(arguments.get("limit", 50)),
                )
                return [TextContent(type="text", text=json.dumps(_json_safe({"documents": results, "count": len(results)}), ensure_ascii=False))]

            elif name == "rag_delete":
                success = rag.delete(arguments["document_id"])
                return [TextContent(type="text", text=json.dumps(_json_safe({"success": success}), ensure_ascii=False))]

            elif name == "rag_ingest_dir":
                import fnmatch
                from pathlib import Path

                dir_path = Path(arguments["path"])
                if not dir_path.is_dir():
                    return [TextContent(type="text", text=json.dumps({"error": f"{arguments['path']} is not a directory"}))]

                patterns = [p.strip() for p in arguments.get("pattern", "*.md,*.txt,*.rst").split(",")]
                ignore = [p.strip() for p in arguments.get("ignore", "node_modules,.git,__pycache__,.venv").split(",")]
                tags = arguments.get("tags", [])

                results = []
                for file_path in sorted(dir_path.rglob("*")):
                    if not file_path.is_file():
                        continue
                    rel_path = file_path.relative_to(dir_path)
                    if any(ig in rel_path.parts for ig in ignore):
                        continue
                    if not any(fnmatch.fnmatch(file_path.name, p) for p in patterns):
                        continue
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        if len(content) < 50:
                            continue
                        result = rag.ingest(
                            source=str(rel_path),
                            content=content,
                            title=file_path.stem,
                            domain=arguments.get("domain"),
                            tags=tags,
                        )
                        results.append({"file": str(rel_path), **result})
                    except Exception as e:
                        results.append({"file": str(rel_path), "error": str(e)})

                return [TextContent(type="text", text=json.dumps(_json_safe({"imported": len(results), "files": results}), ensure_ascii=False))]

            elif name == "rag_ingest_url":
                import re
                import urllib.request

                try:
                    req = urllib.request.Request(arguments["url"], headers={"User-Agent": "my-agent-memory/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        content = resp.read().decode("utf-8", errors="ignore")

                    if "<html" in content.lower()[:500]:
                        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
                        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
                        content = re.sub(r"<[^>]+>", "", content)
                        content = re.sub(r"\s+", " ", content)
                        content = content.strip()

                    if len(content) < 50:
                        return [TextContent(type="text", text=json.dumps({"error": "Content too short"}))]

                    result = rag.ingest(
                        source=arguments["url"],
                        content=content,
                        domain=arguments.get("domain"),
                        tags=arguments.get("tags"),
                    )
                    return [TextContent(type="text", text=json.dumps(_json_safe({"status": "ingested", **result}), ensure_ascii=False))]
                except Exception as e:
                    return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

            # Learning tools
            elif name == "memory_learn":
                tags = arguments.get("tags", [])
                if arguments.get("domain"):
                    tags.append(f"domain:{arguments['domain']}")
                entry = store.save(
                    content=arguments["content"],
                    title=arguments.get("title", ""),
                    tags=tags,
                    scope="private",
                    memory_type=arguments.get("learned_type", "learned-solution"),
                )
                entry.pop("embedding", None)
                return [TextContent(type="text", text=json.dumps(_json_safe({"status": "learned", "entry": entry}), ensure_ascii=False))]

            elif name == "memory_unified_search":
                query = arguments["query"]
                domain = arguments.get("domain")
                limit = int(arguments.get("limit", 5))

                # Search structured memories
                memories = store.hybrid_search(query, limit=limit)
                for m in memories:
                    m.pop("embedding", None)

                # Search learned memories
                learned = store.hybrid_search(query, limit=limit, memory_type="learned-*")
                for l in learned:
                    l.pop("embedding", None)

                # Search RAG
                rag_results = rag.search(query, domain=domain, limit=limit)

                return [TextContent(type="text", text=json.dumps(_json_safe({
                    "memories": memories,
                    "learned": learned,
                    "rag": rag_results,
                    "total": len(memories) + len(learned) + len(rag_results),
                }), ensure_ascii=False))]

            else:
                return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return server


async def run_mcp_server(db_path: str = "", agent_id: str = "claude-code"):
    """Run the MCP server via stdio transport."""
    server = create_server(db_path=db_path, agent_id=agent_id)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="my-agent-memory MCP server")
    parser.add_argument("--db-path", default="", help="SQLite database path")
    parser.add_argument("--agent-id", default="claude-code", help="Agent identifier")
    args = parser.parse_args()

    import asyncio
    asyncio.run(run_mcp_server(db_path=args.db_path, agent_id=args.agent_id))


if __name__ == "__main__":
    main()
