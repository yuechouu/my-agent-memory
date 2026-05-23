"""Tests for scoring module."""

import math
from datetime import datetime, timezone, timedelta

from my_agent_memory.scoring import compute_score, compute_scores_for_entries


class TestComputeScore:
    def test_basic_score(self):
        """Entry with 3 accesses today, manual source."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        ts = now.isoformat()
        score = compute_score(access_count=3, last_access_ts=ts, source="manual", now=now)
        # log2(4) * 1.0 * 1.0 = 2.0
        assert score == 2.0

    def test_zero_access(self):
        """Entry with 0 accesses."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        ts = now.isoformat()
        score = compute_score(access_count=0, last_access_ts=ts, source="manual", now=now)
        # log2(1) * 1.0 * 1.0 = 0.0
        assert score == 0.0

    def test_time_decay_half_life(self):
        """Score should halve after half_life_days."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        ts = (now - timedelta(days=30)).isoformat()
        score = compute_score(access_count=3, last_access_ts=ts, source="manual", half_life_days=30, now=now)
        # log2(4) * 0.5 * 1.0 = 1.0
        assert abs(score - 1.0) < 0.01

    def test_source_weight_agent(self):
        """Agent source has weight 0.8."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        ts = now.isoformat()
        score = compute_score(access_count=3, last_access_ts=ts, source="agent", now=now)
        # log2(4) * 1.0 * 0.8 = 1.6
        assert abs(score - 1.6) < 0.01

    def test_source_weight_imported(self):
        """Imported source has weight 0.5."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        ts = now.isoformat()
        score = compute_score(access_count=3, last_access_ts=ts, source="imported", now=now)
        # log2(4) * 1.0 * 0.5 = 1.0
        assert abs(score - 1.0) < 0.01

    def test_none_last_access(self):
        """None last_access_ts should treat as just accessed (days=0)."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        score = compute_score(access_count=3, last_access_ts=None, source="manual", now=now)
        assert score == 2.0

    def test_naive_timestamp(self):
        """Naive timestamp (no timezone) should be treated as UTC."""
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        ts = "2025-01-15T00:00:00"  # naive
        score = compute_score(access_count=3, last_access_ts=ts, source="manual", now=now)
        assert score == 2.0


class TestComputeScoresForEntries:
    def test_returns_sorted(self):
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        entries = [
            {"id": 1, "access_count": 1, "last_access_ts": now.isoformat(), "source": "manual"},
            {"id": 2, "access_count": 10, "last_access_ts": now.isoformat(), "source": "manual"},
            {"id": 3, "access_count": 3, "last_access_ts": now.isoformat(), "source": "manual"},
        ]
        results = compute_scores_for_entries(entries, now=now)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_entry_ids_preserved(self):
        now = datetime(2025, 1, 15, tzinfo=timezone.utc)
        entries = [
            {"id": 42, "access_count": 5, "last_access_ts": now.isoformat(), "source": "manual"},
        ]
        results = compute_scores_for_entries(entries, now=now)
        assert results[0][0] == 42
