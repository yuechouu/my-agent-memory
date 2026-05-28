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
