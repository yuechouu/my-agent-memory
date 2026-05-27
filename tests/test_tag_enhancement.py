"""Tests for smart tag enhancement."""

import os
os.environ["HERMES_AGENT_ID"] = "test"

from my_agent_memory.store import MultiAgentStore
from my_agent_memory.validate import validate_tags, DEFAULT_TAG_BLACKLIST
from my_agent_memory.llm import build_suggest_tags_messages, parse_suggest_tags_response


def make_store():
    return MultiAgentStore(db_path=":memory:", agent_id="test", config={})


class TestGetTagFrequencies:
    def test_returns_sorted_counts(self):
        store = make_store()
        store.save("A", tags=["python", "docker"])
        store.save("B", tags=["python", "server"])
        store.save("C", tags=["python", "flask"])
        freq = store.db.get_tag_frequencies()
        tag_map = {f["tag"]: f["count"] for f in freq}
        assert tag_map["python"] == 3
        assert tag_map["docker"] == 1
        assert tag_map["server"] == 1
        assert tag_map["flask"] == 1
        # python should be first (most frequent)
        assert freq[0]["tag"] == "python"
        store.close()

    def test_empty_db(self):
        store = make_store()
        freq = store.db.get_tag_frequencies()
        assert freq == []
        store.close()

    def test_limit(self):
        store = make_store()
        store.save("A", tags=["a", "b", "c", "d", "e"])
        freq = store.db.get_tag_frequencies(limit=3)
        assert len(freq) == 3
        store.close()


class TestValidateTags:
    def test_accepts_valid_tags(self):
        valid, err = validate_tags(["python", "machine-learning", "fastapi"])
        assert valid is True
        assert err is None

    def test_rejects_blacklisted(self):
        for tag in DEFAULT_TAG_BLACKLIST:
            valid, err = validate_tags([tag])
            assert valid is False, f"Tag '{tag}' should be rejected"
            assert "blacklisted" in err.lower()

    def test_rejects_invalid_format(self):
        valid, err = validate_tags(["has space"])
        assert valid is False
        valid, err = validate_tags(["special@char"])
        assert valid is False
        valid, err = validate_tags(["123"])  # starts with digit is OK actually
        assert valid is True  # ^[a-z0-9] allows digits

    def test_normalizes_case(self):
        """UPPERCASE is normalized to lowercase — this is valid."""
        valid, _ = validate_tags(["PYTHON"])
        assert valid is True

    def test_rejects_duplicate(self):
        valid, err = validate_tags(["python", "Python"])
        assert valid is False
        assert "duplicate" in err.lower()

    def test_rejects_empty(self):
        valid, err = validate_tags([""])
        assert valid is False

    def test_accepts_hyphenated(self):
        valid, _ = validate_tags(["machine-learning", "ci-cd", "node_js"])
        assert valid is True

    def test_empty_list_is_valid(self):
        valid, _ = validate_tags([])
        assert valid is True


class TestBuildSuggestTagsMessages:
    def test_includes_existing_tags(self):
        existing = [{"tag": "python", "count": 12}, {"tag": "docker", "count": 5}]
        msgs = build_suggest_tags_messages("Title", "Content", "entity", existing)
        prompt = msgs[0]["content"]
        assert "python (12)" in prompt
        assert "docker (5)" in prompt
        assert "entity" in prompt

    def test_includes_memory_type(self):
        msgs = build_suggest_tags_messages("Title", "Content", "procedural")
        prompt = msgs[0]["content"]
        assert "procedural" in prompt

    def test_no_existing_tags(self):
        msgs = build_suggest_tags_messages("Title", "Content", "knowledge", None)
        prompt = msgs[0]["content"]
        assert "(none yet)" in prompt


class TestParseSuggestTagsResponse:
    def test_json_array(self):
        tags = parse_suggest_tags_response('["python", "docker", "flask"]')
        assert tags == ["python", "docker", "flask"]

    def test_comma_separated(self):
        tags = parse_suggest_tags_response("python, docker, flask")
        assert tags == ["python", "docker", "flask"]

    def test_mixed_case_normalized(self):
        tags = parse_suggest_tags_response('["Python", "DOCKER"]')
        assert tags == ["python", "docker"]

    def test_limits_to_five(self):
        tags = parse_suggest_tags_response('["a", "b", "c", "d", "e", "f", "g"]')
        assert len(tags) == 5

    def test_empty_response(self):
        tags = parse_suggest_tags_response("")
        assert tags == []
