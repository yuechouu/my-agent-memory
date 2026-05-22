"""Scoring formula for memory entry lifecycle management.

score = log2(access_count + 1) x e^(-lambda x days_since_last_access) x source_weight

Where:
  lambda = ln(2) / half_life_days
  half_life_days = 30 (default)
  source_weight: manual=1.0, agent=0.8, imported=0.5, consolidated=0.9

The score is recalculated for all active entries during each dreaming run.
"""

import math
from datetime import datetime, timezone
from typing import Optional


# Configurable scoring parameters
DEFAULT_HALF_LIFE_DAYS = 30
DEFAULT_SOURCE_WEIGHTS = {
    "manual": 1.0,
    "agent": 0.8,
    "imported": 0.5,
    "consolidated": 0.9,
}


def compute_score(
    access_count: int,
    last_access_ts: Optional[str],
    source: str = "manual",
    half_life_days: int = DEFAULT_HALF_LIFE_DAYS,
    now: Optional[datetime] = None,
) -> float:
    """Compute the current score for a memory entry.

    Args:
        access_count: Number of times this entry was accessed (search hits, get calls).
        last_access_ts: ISO 8601 timestamp of last access. If None, uses current time.
        source: Source type (manual, agent, imported, consolidated).
        half_life_days: Days after which the time decay factor halves.
        now: Current time for testing. Defaults to datetime.now(UTC).

    Returns:
        Score as a float. Higher = more relevant.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Frequency component: log2 dampens high access counts
    freq = math.log2(access_count + 1)

    # Time decay component: exponential forgetting curve
    if last_access_ts:
        last_access = datetime.fromisoformat(last_access_ts)
        if last_access.tzinfo is None:
            last_access = last_access.replace(tzinfo=timezone.utc)
        days = (now - last_access).total_seconds() / 86400.0
    else:
        days = 0.0

    lambda_val = math.log(2) / half_life_days
    decay = math.exp(-lambda_val * days)

    # Source weight component
    weight = DEFAULT_SOURCE_WEIGHTS.get(source, 0.5)

    score = freq * decay * weight
    return round(score, 4)


def compute_scores_for_entries(
    entries: list[dict],
    half_life_days: int = DEFAULT_HALF_LIFE_DAYS,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Compute scores for a list of entries, returning (entry_id, score) pairs.

    Args:
        entries: List of entry dicts with keys: id, access_count, last_access_ts, source.
        half_life_days: Days after which the time decay factor halves.
        now: Current time for testing.

    Returns:
        List of (entry_id, score) tuples sorted by score descending.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    results = []
    for entry in entries:
        score = compute_score(
            access_count=entry.get("access_count", 0),
            last_access_ts=entry.get("last_access_ts"),
            source=entry.get("source", "manual"),
            half_life_days=half_life_days,
            now=now,
        )
        results.append((entry["id"], score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
