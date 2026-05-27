"""Semantic reranker using SiliconFlow's reranker API.

Two-stage retrieval: FTS5+vector+RRF produces candidates, then
the Reranker re-scores them with a cross-encoder for higher precision.

Error strategy: best-effort. Returns original order on failure.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Optional

__all__ = ["Reranker"]

logger = logging.getLogger("my-agent-memory.reranker")

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_TIMEOUT = 30


class Reranker:
    """Semantic reranker using cross-encoder model via SiliconFlow API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def rerank(
        self,
        query: str,
        passages: list[dict],
        content_key: str = "content",
        title_key: str = "title",
        top_n: int = 10,
    ) -> list[dict]:
        """Rerank passages by semantic relevance to query.

        Args:
            query: The search query.
            passages: List of dicts with content_key field.
            content_key: Key to extract text from each passage.
            title_key: Key to extract title (prepended to content).
            top_n: Number of top results to return.

        Returns:
            Reranked list of passages with 'rerank_score' added to each.
            Returns original order on failure.
        """
        if not passages or not self.api_key:
            return passages[:top_n]

        # Build document texts
        docs = []
        for p in passages:
            title = p.get(title_key, "")
            content = p.get(content_key, "")
            text = f"{title}: {content}" if title else content
            docs.append(text[:2000])  # truncate for API limits

        try:
            results = self._call_api(query, docs)
        except Exception as e:
            logger.debug("Reranker failed, returning original order: %s", e)
            return passages[:top_n]

        # Map scores back to passages
        scored = []
        for item in results:
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0.0)
            if 0 <= idx < len(passages):
                entry = dict(passages[idx])
                entry["rerank_score"] = round(score, 4)
                scored.append(entry)

        # Sort by rerank_score descending
        scored.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return scored[:top_n]

    def _call_api(self, query: str, documents: list[str]) -> list[dict]:
        """Call SiliconFlow reranker API."""
        url = f"{self.base_url}/rerank"
        payload = json.dumps({
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
            "return_documents": False,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Reranker HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Reranker connection error: {e.reason}")

        if "results" not in body:
            raise RuntimeError(f"Unexpected reranker response: {body}")

        return body["results"]
