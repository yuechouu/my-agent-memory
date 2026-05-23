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

__all__ = ["LLMClient", "LLMError", "build_consolidate_messages", "parse_consolidate_response"]


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
