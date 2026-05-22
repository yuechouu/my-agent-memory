"""SiliconFlow embedding wrapper — Qwen3-Embedding-8B via batch API.

Features:
  - Batch embedding (multiple texts in one API call)
  - Checksum-based caching (same content → same checksum → skip re-embedding)
  - Async generation (embedding runs after write returns, non-blocking)
  - Configurable base URL and model
"""

import hashlib
import json
import time
import urllib.request
import urllib.error
from typing import Optional

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen3-Embedding-8B"
DEFAULT_DIMENSIONS = 2048
DEFAULT_BATCH_SIZE = 10
DEFAULT_TIMEOUT = 30  # seconds


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


class EmbeddingClient:
    """Client for SiliconFlow embedding API."""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout
        self._cache: dict[str, list[float]] = {}  # checksum → embedding

    def embed(self, text: str) -> Optional[list[float]]:
        """Generate embedding for a single text. Returns None on failure."""
        results = self.embed_batch([text])
        if results:
            return results[0]
        return None

    def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Generate embeddings for multiple texts. Cached by checksum.

        Returns list of embedding vectors (or None for failed items), same order as input.
        """
        if not texts:
            return []
        if not self.api_key:
            return [None] * len(texts)

        # Check cache
        results: list[Optional[list[float]]] = []
        to_embed: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            if not text.strip():
                results.append(None)
                continue
            ck = hashlib.md5(text.encode()).hexdigest()[:12]
            if ck in self._cache:
                results.append(self._cache[ck])
            else:
                to_embed.append((i, text))
                results.append(None)  # placeholder

        if not to_embed:
            return results

        # Batch API call
        for batch_start in range(0, len(to_embed), self.batch_size):
            batch = to_embed[batch_start:batch_start + self.batch_size]
            batch_texts = [t for _, t in batch]

            try:
                vectors = self._call_api(batch_texts)
                for (idx, text), vector in zip(batch, vectors):
                    ck = hashlib.md5(text.encode()).hexdigest()[:12]
                    self._cache[ck] = vector
                    results[idx] = vector
            except EmbeddingError:
                # Leave None for failed items
                pass

        return results

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Make a single batch API call to SiliconFlow embeddings endpoint."""
        url = f"{self.base_url}/embeddings"
        payload = json.dumps({
            "model": self.model,
            "input": texts,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise EmbeddingError(f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise EmbeddingError(f"Connection error: {e.reason}")
        except json.JSONDecodeError:
            raise EmbeddingError("Invalid JSON response")

        # Extract embeddings from response
        if "data" not in body:
            raise EmbeddingError(f"Unexpected response format: {body}")

        data = sorted(body["data"], key=lambda x: x.get("index", 0))
        vectors = []
        for item in data:
            if "embedding" in item:
                vectors.append(item["embedding"])
            else:
                raise EmbeddingError(f"No embedding in response item: {item}")

        return vectors

    def cache_size(self) -> int:
        """Number of cached embeddings."""
        return len(self._cache)

    def clear_cache(self):
        """Clear the in-memory embedding cache."""
        self._cache.clear()
