"""Web management dashboard server — lightweight HTTP server.

Usage:
  my-agent-memory serve --port 8765

Binds to 127.0.0.1 only (localhost). No authentication.
"""

import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from pathlib import Path

from my_agent_memory.store import MultiAgentStore


def _json_safe(obj):
    """Recursively convert bytes fields to None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    elif isinstance(obj, bytes):
        return None
    return obj


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the memory dashboard."""

    store: MultiAgentStore = None

    def log_message(self, format, *args):
        """Suppress default logging to stderr."""
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/":
            self._serve_html()
        elif path == "/api/entries":
            self._handle_json(self._list_entries(params))
        elif path.startswith("/api/entries/"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.get(eid))
        elif path == "/api/conflicts":
            self._handle_json(self.store.get_conflicts("open"))
        elif path == "/api/stats":
            self._handle_json(self.store.stats())
        elif path == "/api/search":
            self._handle_json(self._fts_search(params))
        elif path == "/api/hybrid":
            self._handle_json(self._hybrid_search(params))
        elif path == "/api/tag-graph":
            self._handle_json(self._tag_graph(params))
        elif path == "/api/system-prompt":
            agent = params.get("agent", self.store.agent_id)
            max_chars = int(params["max_chars"]) if params.get("max_chars") else None
            self._handle_json({"agent": agent, "content": self.store.get_system_prompt_block(agent_id=agent, max_chars=max_chars)})
        elif path == "/api/dreaming-log":
            self._handle_json(self.store.db.get_dreaming_log(limit=int(params.get("limit", 20))))
        elif path == "/api/audit-log":
            self._handle_json(self.store.db.get_audit_log(
                entry_id=int(params["entry_id"]) if params.get("entry_id") else None,
                action=params.get("action"),
                agent_id=params.get("agent"),
                limit=int(params.get("limit", 50)),
            ))
        else:
            self._error(404, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/api/entries":
            self._handle_json(self.store.save(**body))
        elif path.startswith("/api/entries/") and path.endswith("/archive"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.archive(eid))
        elif path.startswith("/api/entries/") and path.endswith("/share"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.share(eid))
        elif path.startswith("/api/entries/") and path.endswith("/unshare"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.unshare(eid))
        elif path.startswith("/api/entries/") and path.endswith("/pin"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.pin(eid))
        elif path.startswith("/api/entries/") and path.endswith("/unpin"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.unpin(eid))
        elif path == "/api/dreaming":
            dry_run = body.get("dry_run", True)
            self._handle_json(self.store.dreaming(dry_run=dry_run))
        elif path.startswith("/api/conflicts/") and path.endswith("/resolve"):
            cid = int(path.split("/")[3])
            self._handle_json(self.store.resolve_conflict(
                conflict_id=cid,
                strategy=body.get("strategy", "dismiss"),
                merged_content=body.get("merged_content"),
            ))
        else:
            self._error(404, "Not found")

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path.startswith("/api/entries/"):
            eid = int(path.split("/")[3])
            self._handle_json(self.store.update(eid, **body))
        else:
            self._error(404, "Not found")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/entries/"):
            eid = int(path.split("/")[3])
            success = self.store.delete(eid)
            self._handle_json({"ok": success, "id": eid})
        else:
            self._error(404, "Not found")

    def _fts_search(self, params: dict):
        query = params.get("q", "")
        if not query:
            return {"error": "Missing query parameter 'q'", "results": []}
        return self.store.search(
            query,
            limit=int(params.get("limit", 10)),
            scope=params.get("scope"),
            agent_id=params.get("agent"),
            memory_type=params.get("memory_type"),
        )

    def _hybrid_search(self, params: dict):
        query = params.get("q", "")
        if not query:
            return {"error": "Missing query parameter 'q'", "results": []}
        return self.store.hybrid_search(
            query,
            limit=int(params.get("limit", 10)),
            agent_id=params.get("agent", "*"),
            scope=params.get("scope"),
            project=params.get("project"),
            memory_type=params.get("memory_type"),
            rerank=params.get("rerank", "").lower() == "true",
        )

    def _tag_graph(self, params: dict):
        action = params.get("action", "stats")
        tag = params.get("tag", "")
        if action == "related" and tag:
            return {"tag": tag, "related": self.store.tag_graph.get_related_tags(tag)}
        return self.store.tag_graph.get_tag_stats()

    def _list_entries(self, params: dict):
        return self.store.list_entries(
            agent_id=params.get("agent"),
            scope=params.get("scope"),
            state=params.get("state"),
            page=int(params.get("page", 1)),
            limit=int(params.get("limit", 10)),
            query=params.get("q"),
            sort_by=params.get("sort", ""),
            sort_order=params.get("order", "desc"),
        )

    def _handle_json(self, data):
        if data is None:
            self._error(404, "Not found")
            return
        body = json.dumps(_json_safe(data), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html_path = Path(__file__).parent / "static" / "dashboard.html"
        if html_path.exists():
            body = html_path.read_bytes()
        else:
            body = b"<h1>Dashboard not found</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _error(self, code: int, message: str):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def run_server(port: int = 8765, store_factory=None):
    """Start the dashboard HTTP server."""
    if store_factory:
        DashboardHandler.store = store_factory()
    else:
        DashboardHandler.store = MultiAgentStore(agent_id="yuechou")

    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Hermes Memory Dashboard: http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
