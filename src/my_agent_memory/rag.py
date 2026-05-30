"""RAG (Retrieval-Augmented Generation) engine — document ingestion + hybrid search.

Features:
  - Document ingestion with automatic chunking
  - FTS5 keyword search + sqlite-vec semantic search
  - RRF (Reciprocal Rank Fusion) for hybrid retrieval
  - Domain filtering and metadata support
"""

import hashlib
import json
import logging
import re
from typing import Optional

__all__ = ["RAGEngine"]

logger = logging.getLogger(__name__)


class RAGEngine:
    """Document ingestion and hybrid retrieval."""

    def __init__(self, db, embed_client=None):
        """
        Args:
            db: Database instance
            embed_client: EmbeddingClient for vector search (optional)
        """
        self.db = db
        self.embed = embed_client

    def ingest(
        self,
        source: str,
        content: str,
        title: Optional[str] = None,
        domain: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Ingest a document: chunk + embed + store.

        Returns:
            {"document_id": str, "chunk_count": int}
        """
        doc_id = hashlib.md5(source.encode()).hexdigest()
        content_hash = hashlib.md5(content.encode()).hexdigest()

        # Check if already ingested with same content
        existing = self.db.get_rag_document(doc_id)
        if existing and existing.get("content_hash") == content_hash:
            logger.info(f"Document already ingested: {source}")
            return {"document_id": doc_id, "chunk_count": existing["chunk_count"]}

        # 1. Split into chunks
        chunks = self._split_chunks(content, source)
        logger.info(f"Split {source} into {len(chunks)} chunks")

        # 2. Store document metadata
        self.db.upsert_rag_document(
            doc_id=doc_id,
            source=source,
            title=title or self._extract_title(content) or source,
            domain=domain,
            tags=tags or [],
            content_hash=content_hash,
            chunk_count=len(chunks),
            metadata=metadata or {},
        )

        # 3. Store chunks
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_{i}"
            self.db.upsert_rag_chunk(
                chunk_id=chunk_id,
                document_id=doc_id,
                chunk_index=i,
                content=chunk["content"],
                heading=chunk.get("heading"),
                start_line=chunk.get("start_line"),
                end_line=chunk.get("end_line"),
            )

        # 4. Generate embeddings (async if available)
        if self.embed:
            self._embed_chunks(doc_id, chunks)

        return {"document_id": doc_id, "chunk_count": len(chunks)}

    def search(
        self,
        query: str,
        domain: Optional[str] = None,
        limit: int = 5,
        use_reranker: bool = True,
    ) -> list[dict]:
        """Hybrid search: FTS5 + vector + RRF.

        Returns:
            List of chunks with scores
        """
        results = []

        # 1. FTS5 keyword search
        fts_results = self.db.search_rag_fts(query, limit=limit * 2)
        logger.debug(f"FTS returned {len(fts_results)} results")

        # 2. Vector semantic search (if embedding available)
        vec_results = []
        if self.embed:
            try:
                query_vec = self.embed.embed(query)
                if query_vec:
                    vec_results = self.db.search_rag_vec(query_vec, limit=limit * 2)
                    logger.debug(f"Vector returned {len(vec_results)} results")
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # 3. RRF fusion
        if vec_results:
            fused = self._rrf_fusion(fts_results, vec_results, k=60)
        else:
            # Fallback to FTS only
            fused = fts_results
            for r in fused:
                r["rrf_score"] = r.get("rank", 0)

        # 4. Filter by domain
        if domain:
            fused = [r for r in fused if r.get("domain") == domain]

        # 5. Enrich with document metadata
        for result in fused:
            doc = self.db.get_rag_document(result["document_id"])
            if doc:
                result["source"] = doc.get("source")
                result["title"] = doc.get("title")
                result["domain"] = doc.get("domain")

        return fused[:limit]

    def delete(self, document_id: str) -> bool:
        """Delete a document and all its chunks."""
        return self.db.delete_rag_document(document_id)

    def sync(self, remove_orphans: bool = False) -> dict:
        """Sync RAG documents with source files.

        Checks if source files still exist and updates status.

        Args:
            remove_orphans: If True, delete RAG entries for missing sources

        Returns:
            Dict with sync results
        """
        from pathlib import Path
        import urllib.request

        documents = self.list_documents(limit=1000)
        result = {
            "total": len(documents),
            "valid": 0,
            "missing": 0,
            "updated": 0,
            "removed": 0,
            "missing_docs": [],
        }

        for doc in documents:
            source = doc.get("source", "")

            # Check if source is a local file
            if Path(source).exists():
                # Check if content has changed
                try:
                    current_content = Path(source).read_text(encoding="utf-8")
                    current_hash = hashlib.md5(current_content.encode()).hexdigest()
                    if current_hash != doc.get("content_hash"):
                        # Content changed, re-ingest
                        self.ingest(
                            source=source,
                            content=current_content,
                            title=doc.get("title"),
                            domain=doc.get("domain"),
                            tags=doc.get("tags"),
                        )
                        result["updated"] += 1
                    else:
                        result["valid"] += 1
                except Exception:
                    result["valid"] += 1
            elif source.startswith("http"):
                # URL - try to fetch
                try:
                    req = urllib.request.Request(source, method="HEAD", headers={"User-Agent": "my-agent-memory/1.0"})
                    urllib.request.urlopen(req, timeout=10)
                    result["valid"] += 1
                except Exception:
                    result["missing"] += 1
                    result["missing_docs"].append({"id": doc["id"], "source": source})
                    if remove_orphans:
                        self.delete(doc["id"])
                        result["removed"] += 1
            else:
                # Unknown source type
                result["valid"] += 1

        return result

    def cleanup(self) -> dict:
        """Remove RAG entries for missing sources."""
        return self.sync(remove_orphans=True)

    def list_documents(
        self,
        domain: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """List ingested documents."""
        return self.db.list_rag_documents(domain=domain, limit=limit)

    def get_document(self, document_id: str) -> Optional[dict]:
        """Get document metadata."""
        return self.db.get_rag_document(document_id)

    def _split_chunks(self, content: str, source: str) -> list[dict]:
        """Smart chunking: by Markdown headings + paragraphs.

        Rules:
        - Split on headings (# ## ### etc.)
        - Merge small chunks (< 100 chars)
        - Split large chunks (> 2000 chars) by paragraphs
        """
        lines = content.split("\n")
        chunks = []
        current_heading = ""
        current_lines = []
        line_start = 1

        for i, line in enumerate(lines, 1):
            if re.match(r"^#{1,6}\s+", line):
                # Save current chunk
                if current_lines:
                    chunk_content = "\n".join(current_lines).strip()
                    if chunk_content:
                        chunks.append({
                            "heading": current_heading,
                            "content": chunk_content,
                            "start_line": line_start,
                            "end_line": i - 1,
                        })
                current_heading = re.sub(r"^#{1,6}\s+", "", line).strip()
                current_lines = []
                line_start = i
            else:
                current_lines.append(line)

        # Last chunk
        if current_lines:
            chunk_content = "\n".join(current_lines).strip()
            if chunk_content:
                chunks.append({
                    "heading": current_heading,
                    "content": chunk_content,
                    "start_line": line_start,
                    "end_line": len(lines),
                })

        # Merge small chunks
        merged = self._merge_small_chunks(chunks, min_size=100)

        # Split large chunks
        result = []
        for chunk in merged:
            if len(chunk["content"]) > 2000:
                result.extend(self._split_large_chunk(chunk))
            else:
                result.append(chunk)

        return result

    def _merge_small_chunks(self, chunks: list[dict], min_size: int = 100) -> list[dict]:
        """Merge chunks smaller than min_size with adjacent chunks."""
        if not chunks:
            return []

        merged = [chunks[0]]
        for chunk in chunks[1:]:
            if len(merged[-1]["content"]) < min_size:
                # Merge with previous
                merged[-1]["content"] += "\n\n" + chunk["content"]
                merged[-1]["end_line"] = chunk["end_line"]
            else:
                merged.append(chunk)

        # Check last chunk
        if len(merged) > 1 and len(merged[-1]["content"]) < min_size:
            merged[-2]["content"] += "\n\n" + merged[-1]["content"]
            merged[-2]["end_line"] = merged[-1]["end_line"]
            merged.pop()

        return merged

    def _split_large_chunk(self, chunk: dict, max_size: int = 2000) -> list[dict]:
        """Split a large chunk by paragraphs."""
        paragraphs = chunk["content"].split("\n\n")
        result = []
        current = []
        current_size = 0
        line_offset = chunk["start_line"]

        for para in paragraphs:
            if current_size + len(para) > max_size and current:
                result.append({
                    "heading": chunk["heading"],
                    "content": "\n\n".join(current),
                    "start_line": line_offset,
                    "end_line": line_offset + sum(p.count("\n") for p in current),
                })
                current = []
                current_size = 0
            current.append(para)
            current_size += len(para)

        if current:
            result.append({
                "heading": chunk["heading"],
                "content": "\n\n".join(current),
                "start_line": line_offset,
                "end_line": chunk["end_line"],
            })

        return result

    def _extract_title(self, content: str) -> Optional[str]:
        """Extract title from first heading in content."""
        match = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else None

    def _embed_chunks(self, doc_id: str, chunks: list[dict]):
        """Batch embed chunks and store vectors."""
        try:
            texts = [c["content"] for c in chunks]
            embeddings = self.embed.embed_batch(texts)

            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                if emb:
                    chunk_id = f"{doc_id}_{i}"
                    self.db.update_rag_chunk_embedding(chunk_id, emb)

            logger.info(f"Embedded {len(embeddings)} chunks for {doc_id}")
        except Exception as e:
            logger.error(f"Failed to embed chunks: {e}")

    def _rrf_fusion(
        self,
        fts_results: list[dict],
        vec_results: list[dict],
        k: int = 60,
    ) -> list[dict]:
        """Reciprocal Rank Fusion for combining FTS and vector results.

        RRF score = sum(1 / (k + rank_i)) for each result list
        """
        scores = {}
        data = {}

        # FTS scores
        for rank, result in enumerate(fts_results):
            chunk_id = result["id"]
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)
            data[chunk_id] = result

        # Vector scores
        for rank, result in enumerate(vec_results):
            chunk_id = result["id"]
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)
            if chunk_id not in data:
                data[chunk_id] = result

        # Sort by RRF score
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        result = []
        for chunk_id in sorted_ids:
            entry = data[chunk_id].copy()
            entry["rrf_score"] = scores[chunk_id]
            result.append(entry)

        return result
