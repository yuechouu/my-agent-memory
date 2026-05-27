"""Scoring formula for memory entry lifecycle management.

score = log2(access_count + 1) x e^(-lambda x days_since_last_access) x source_weight

Where:
  lambda = ln(2) / half_life_days
  half_life_days: per memory_type (None = no decay)
  source_weight: manual=1.0, agent=0.8, imported=0.5, consolidated=0.9

The score is recalculated for all active entries during each dreaming run.
"""

import math
from datetime import datetime, timezone
from typing import Optional

from my_agent_memory.memory_types import get_type_config


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
    memory_type: str = "knowledge",
    half_life_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> float:
    """Compute the current score for a memory entry.

    Args:
        access_count: Number of times this entry was accessed (search hits, get calls).
        last_access_ts: ISO 8601 timestamp of last access. If None, uses current time.
        source: Source type (manual, agent, imported, consolidated).
        memory_type: Memory type (procedural, entity, knowledge). Used for decay.
        half_life_days: Override for time decay. None = use type default.
        now: Current time for testing. Defaults to datetime.now(UTC).

    Returns:
        Score as a float. Higher = more relevant.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Resolve half_life_days from type config if not explicitly provided
    if half_life_days is None:
        type_cfg = get_type_config(memory_type)
        half_life_days = type_cfg.get("half_life_days")

    # Frequency component: log2 dampens high access counts
    freq = math.log2(access_count + 1)

    # Time decay: if half_life_days is None (no decay), decay = 1.0
    if half_life_days is None:
        decay = 1.0
    else:
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
    half_life_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[tuple[int, float]]:
    """Compute scores for a list of entries, returning (entry_id, score) pairs.

    Args:
        entries: List of entry dicts with keys: id, access_count, last_access_ts, source, memory_type.
        half_life_days: Override for time decay. None = use per-type defaults.
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
            memory_type=entry.get("memory_type", "knowledge"),
            half_life_days=half_life_days,
            now=now,
        )
        results.append((entry["id"], score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
