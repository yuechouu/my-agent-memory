"""Input validation for memory entries — injection scanning and content sanitization.

Two-layer defense:
  1. Synchronous gate (simple rules) — runs on every write, rejects immediately.
  2. Async LLM semantic check — runs after gate passes, flags suspicious content.

Imported and adapted from hanako's injection scanning logic.
"""

import re
from typing import Optional, Tuple


# ── Known injection patterns ────────────────────────────────

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|messages?|prompts?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|messages?|prompts?)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|messages?|prompts?)", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\s+are\b", re.IGNORECASE),
    re.compile(r"your\s+(new\s+)?system\s+(prompt|message|instruction)s?\s+(is|are)\b", re.IGNORECASE),
    re.compile(r"curl\s+.*\$(\w*API[_\s]?KEY\w*|token)", re.IGNORECASE),
    re.compile(r"curl\s+.*Authorization\s*:", re.IGNORECASE),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<\|assistant\|>|<\|user\|>|<\|system\|>", re.IGNORECASE),
]

# ── Invisible Unicode characters (zero-width, BOM, direction control) ──

_INVISIBLE_CHARS = {
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a",  # left-to-right embedding
    "\u202b",  # right-to-left embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # left-to-right override
    "\u202e",  # right-to-left override
    "\u2060",  # word joiner
    "\u2061",  # function application
    "\u2062",  # invisible times
    "\u2063",  # invisible separator
    "\u2064",  # invisible plus
    "\ufeff",  # zero-width no-break space / BOM
    "\ufff0",  # (reserved)
}

# ── Content size limits ──

MAX_CONTENT_LENGTH = 10000   # per memory entry
MAX_TITLE_LENGTH = 200
MAX_TAG_LENGTH = 50
MAX_TAGS_COUNT = 10


def validate_sync(content: str, title: str = "", tags: list = None) -> Tuple[bool, Optional[str]]:
    """Synchronous validation gate. Runs on every write before storing.

    Returns:
        (is_valid, error_message). If valid, error_message is None.
    """
    tags = tags or []

    # Size checks
    if not content or not content.strip():
        return False, "Content must not be empty"
    if len(content) > MAX_CONTENT_LENGTH:
        return False, f"Content exceeds maximum length of {MAX_CONTENT_LENGTH} characters"
    if len(title) > MAX_TITLE_LENGTH:
        return False, f"Title exceeds maximum length of {MAX_TITLE_LENGTH} characters"
    if len(tags) > MAX_TAGS_COUNT:
        return False, f"Too many tags (max {MAX_TAGS_COUNT})"

    for tag in tags:
        if len(tag) > MAX_TAG_LENGTH:
            return False, f"Tag '{tag}' exceeds maximum length of {MAX_TAG_LENGTH} characters"

    # Invisible character check (check both content and title)
    text_to_check = content + title
    found_invisible = [c for c in text_to_check if c in _INVISIBLE_CHARS]
    if found_invisible:
        return False, f"Content contains invisible Unicode characters: {_invisible_names(found_invisible[:3])}"

    # Injection pattern check
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(content) or pattern.search(title):
            return False, "Content matches known injection pattern"

    return True, None


def _invisible_names(chars: list[str]) -> str:
    """Convert invisible characters to readable names."""
    import unicodedata
    names = []
    for c in chars:
        try:
            name = unicodedata.name(c, f"U+{ord(c):04X}")
            names.append(name)
        except ValueError:
            names.append(f"U+{ord(c):04X}")
    return ", ".join(names)


def get_invisible_chars(text: str) -> list[str]:
    """Return list of invisible characters found in text (for debugging)."""
    return [c for c in text if c in _INVISIBLE_CHARS]
