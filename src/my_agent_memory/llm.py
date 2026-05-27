"""LLM client for memory consolidation and semantic tasks.

Supports multiple providers via ~/.local/share/kilo/auth.json:
  - xiaomimimo (primary, OpenAI-compatible proxy)
  - deepseek (fallback)

Used for consolidate and conflict checking.

Error strategy: raises LLMError on failure (callers should catch).
"""

import json
import urllib.request
import urllib.error
from typing import Optional, List

from my_agent_memory.config import get_auth_data


DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5-pro"
DEFAULT_TIMEOUT = 60  # seconds

__all__ = ["LLMClient", "LLMError", "build_consolidate_messages", "parse_consolidate_response", "build_extract_messages", "parse_extract_response", "build_suggest_tags_messages", "parse_suggest_tags_response", "build_type_detect_messages", "parse_type_detect_response"]


class LLMError(Exception):
    """Raised when LLM call fails."""
    pass


class LLMClient:
    """Chat completion client with multi-provider support."""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ):
        key, url = self._load_config()
        self.api_key = api_key or key
        self.base_url = (base_url or url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    @staticmethod
    def _load_config() -> tuple[str, str]:
        """Load API key and base_url from auth file via config module.

        Tries xiaomimimo first, then deepseek.
        Returns (api_key, base_url) tuple.
        """
        # Try xiaomimimo first
        xm = get_auth_data("xiaomimimo")
        if xm.get("key"):
            return xm["key"], xm.get("base_url", DEFAULT_BASE_URL)

        # Fallback to deepseek
        ds = get_auth_data("deepseek")
        if ds.get("key"):
            return ds["key"], "https://api.deepseek.com"

        return "", ""

    def chat(self, messages: list[dict], temperature: float = 0.3,
             max_tokens: int = 1000) -> str:
        """Send a chat completion request.

        Args:
            messages: List of {"role": "user"|"system"|"assistant", "content": str}.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Max tokens to generate.

        Returns:
            The assistant's response text.

        Raises:
            LLMError: On API error, network error, or missing key.
        """
        if not self.api_key:
            raise LLMError("API key not configured")

        # Handle base_url that already includes /v1
        if self.base_url.endswith("/v1"):
            url = f"{self.base_url}/chat/completions"
        else:
            url = f"{self.base_url}/v1/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise LLMError(f"HTTP {e.code}: {err_body[:200]}")
        except urllib.error.URLError as e:
            raise LLMError(f"Connection error: {e.reason}")

        if "choices" not in body or not body["choices"]:
            raise LLMError(f"No choices in response: {body}")

        msg = body["choices"][0].get("message", {})
        content = (msg.get("content") or "").strip()
        # Reasoning models (e.g. mimo-v2.5) output in reasoning_content
        if not content:
            content = (msg.get("reasoning_content") or "").strip()
        return content


CONSOLIDATE_SYSTEM_PROMPT = """You are a memory consolidation engine. Your job is to merge multiple related
memory entries into a SINGLE concise, accurate memory entry.

Rules:
1. Merge overlapping information without duplication
2. Resolve contradictions by keeping the most specific/recent fact; note uncertainty if unresolvable
3. Preserve ALL unique information from every entry
4. Keep the result concise but complete — no fluff, no markdown formatting
5. Output ONLY the consolidated memory entry in this exact format:

Title: <clear, specific title>
Content: <merged content, plain text, no markdown>

Do not add explanations, notes, or extra text. Output the title and content ONLY."""


def build_consolidate_messages(entries: list[dict]) -> list[dict]:
    """Build LLM messages for memory consolidation.

    Args:
        entries: List of entry dicts with keys: title, content, source, owner_agent.

    Returns:
        Messages list for LLM chat call.
    """
    parts = []
    for i, e in enumerate(entries):
        parts.append(
            f"Entry {i + 1}:\n"
            f"  Title: {e.get('title', '(untitled)')}\n"
            f"  Content: {e.get('content', '')}\n"
            f"  Source: {e.get('source', 'unknown')} | Agent: {e.get('owner_agent', 'unknown')}"
        )

    user_content = "\n\n".join(parts)
    user_content += "\n\nMerge these entries into one consolidated memory."

    return [
        {"role": "system", "content": CONSOLIDATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_consolidate_response(text: str) -> dict:
    """Parse LLM response into {title, content} dict.

    Args:
        text: Raw LLM response text.

    Returns:
        Dict with 'title' and 'content' keys.
    """
    title = ""
    content = ""

    for line in text.split("\n"):
        line = line.strip()
        if line.lower().startswith("title:") and not title:
            title = line.split(":", 1)[1].strip()
        elif line.lower().startswith("content:") and not content:
            content = line.split(":", 1)[1].strip()
        elif content:
            # Append continuation lines to content
            content += "\n" + line
        elif title and not content:
            # Lines between Title: and Content:
            pass

    # Fallback: if parsing failed, use raw text
    if not title and not content:
        title = "Consolidated"
        content = text.strip()

    return {"title": title.strip('"').strip(), "content": content.strip()}


EXTRACT_SYSTEM_PROMPT = """You are a memory extraction engine. Analyze a conversation turn and extract ONLY durable, reusable facts.

Extract these types of memories:
- User preferences (tools, formats, habits)
- Important facts (server info, credentials references, project details)
- Explicit instructions ("remember this", "always do X", "don't do Y")
- Technical decisions and their rationale

Do NOT extract:
- Greetings, small talk, or pleasantries
- One-time questions without lasting value
- Temporary context (current task status)
- Anything the user explicitly said to forget

If nothing worth remembering, respond ONLY: "NOTHING"

Otherwise respond in this exact format:
Title: <specific, searchable title>
Content: <concise fact, plain text>
Tags: <comma-separated tags, 3-5 words max each>

One memory per conversation turn. Be selective."""


def build_extract_messages(user_msg: str, assistant_msg: str) -> list[dict]:
    """Build LLM messages for memory extraction from a conversation turn."""
    return [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": f"User: {user_msg[:1000]}\n\nAssistant: {assistant_msg[:1000]}"},
    ]


def parse_extract_response(text: str) -> dict | None:
    """Parse LLM extraction response.

    Returns:
        Dict with 'title', 'content', 'tags' keys, or None if nothing to remember.
    """
    upper = text.strip().upper()
    if "NOTHING" in upper and len(text.strip()) < 30:
        return None

    title = ""
    content = ""
    tags = []

    for line in text.split("\n"):
        line = line.strip()
        if line.lower().startswith("title:") and not title:
            title = line.split(":", 1)[1].strip()
        elif line.lower().startswith("content:") and not content:
            content = line.split(":", 1)[1].strip()
        elif line.lower().startswith("tags:"):
            raw = line.split(":", 1)[1].strip()
            tags = [t.strip() for t in raw.split(",") if t.strip()]
        elif content:
            content += "\n" + line

    if not content:
        return None

    return {"title": title.strip('"').strip(), "content": content.strip(), "tags": tags}


TAG_SUGGEST_PROMPT = """Suggest 3-5 tags for this memory entry.

Rules:
- Tags must be lowercase, single words or hyphenated (e.g. "machine-learning", "docker", "python")
- Prefer reusing existing tags when semantically appropriate
- Tags should be specific and searchable, not generic (avoid "misc", "other", "todo")

Title: {title}
Content: {content}
Type: {memory_type}
{existing_tags_section}

Reply ONLY with a JSON array of tags, e.g. ["python", "fastapi", "deployment"]"""


def build_suggest_tags_messages(title: str, content: str, memory_type: str = "",
                                 existing_tags: list = None) -> list[dict]:
    """Build LLM messages for tag suggestion.

    Args:
        title: Entry title.
        content: Entry content.
        memory_type: Memory type (procedural/entity/knowledge).
        existing_tags: Output of db.get_tag_frequencies() — [{"tag": "python", "count": 12}, ...].
    """
    existing_section = ""
    if existing_tags:
        tag_list = ", ".join(f"{t['tag']} ({t['count']})" for t in existing_tags[:30])
        existing_section = f"Existing tags (prefer reuse): {tag_list}"
    else:
        existing_section = "Existing tags: (none yet)"

    return [
        {"role": "user", "content": TAG_SUGGEST_PROMPT.format(
            title=title or "(untitled)",
            content=content[:500],
            memory_type=memory_type or "unknown",
            existing_tags_section=existing_section,
        )},
    ]


def parse_suggest_tags_response(text: str) -> list[str]:
    """Parse LLM tag suggestion response. Tries JSON array first, falls back to comma-split."""
    text = text.strip()
    # Try JSON array
    try:
        import json
        tags = json.loads(text)
        if isinstance(tags, list):
            return [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()][:5]
    except (json.JSONDecodeError, TypeError):
        pass
    # Fallback: comma-separated
    tags = [t.strip().lower() for t in text.split(",") if t.strip()]
    return [t for t in tags if 1 < len(t) <= 30 and " " not in t][:5]


TYPE_DETECT_PROMPT = """Classify this memory entry into exactly one type:

- procedural: How-to steps, workflows, processes, instructions (流程性)
- entity: Facts about specific people, places, tools, services (实体性)
- knowledge: General facts, concepts, theories, configurations (知识性)

Title: {title}
Content: {content}

Reply ONLY with one word: procedural, entity, or knowledge"""


def build_type_detect_messages(title: str, content: str) -> list[dict]:
    """Build LLM messages for memory type detection."""
    return [
        {"role": "user", "content": TYPE_DETECT_PROMPT.format(
            title=title, content=content[:500]
        )},
    ]


def parse_type_detect_response(text: str) -> str:
    """Parse LLM type detection response. Returns type string or empty."""
    lower = text.strip().lower()
    for t in ("procedural", "entity", "knowledge"):
        if t in lower:
            return t
    return ""
