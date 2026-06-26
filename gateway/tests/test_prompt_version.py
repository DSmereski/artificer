"""P4 — prompt-version telemetry tests.

Covers:
- ``prompt_version`` stability (same text → same tag, deterministic)
- ``prompt_version`` cache (repeated calls are cache hits, not re-hashed)
- Two different prompt texts → different tags
- A recorded ``TurnRecord`` carries its ``prompt_version``
- ``query_by_prompt_version`` returns only matching records
- ``group_by_prompt_version`` distinguishes two revisions
"""

from __future__ import annotations

import hashlib

import pytest

from gateway.turn_telemetry import TurnRecord, TurnTelemetry, prompt_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record(
    turn_id: str = "tk-1",
    pv: str = "",
    **kw,
) -> TurnRecord:
    base = dict(
        ts=1.0,
        turn_id=turn_id,
        bot="hive",
        user_msg_preview="hi",
        helpers_used=["planner"],
        total_tokens=100,
        total_latency_ms=500,
        blocked=False,
        error=None,
        actions=[],
        prompt_version=pv,
    )
    base.update(kw)
    return TurnRecord(**base)


# ---------------------------------------------------------------------------
# prompt_version() unit tests
# ---------------------------------------------------------------------------

class TestPromptVersionFunction:
    def test_stable_same_text(self):
        """Same text always returns the same 12-char tag."""
        text = "You are a helpful assistant."
        assert prompt_version(text) == prompt_version(text)

    def test_deterministic_matches_sha256(self):
        """Tag must equal the first 12 chars of the SHA-256 hex digest."""
        text = "planner prompt v1"
        expected = hashlib.sha256(text.encode()).hexdigest()[:12]
        assert prompt_version(text) == expected

    def test_tag_length_is_12(self):
        assert len(prompt_version("any text here")) == 12

    def test_tag_is_hex(self):
        tag = prompt_version("another prompt")
        assert all(c in "0123456789abcdef" for c in tag)

    def test_different_texts_produce_different_tags(self):
        """Two distinct prompt strings must not collide."""
        tag_a = prompt_version("prompt revision A — think step by step")
        tag_b = prompt_version("prompt revision B — be concise")
        assert tag_a != tag_b

    def test_whitespace_sensitivity(self):
        """Even a trailing space is a different prompt."""
        assert prompt_version("hello") != prompt_version("hello ")

    def test_cache_is_used(self):
        """lru_cache: calling with the same text returns the cached result.

        We verify indirectly by checking cache_info() increments hits
        on the second call rather than misses.
        """
        text = "unique-cache-probe-prompt-xyz"
        # Clear and warm once
        prompt_version.cache_clear()
        prompt_version(text)           # miss
        info_after_first = prompt_version.cache_info()

        prompt_version(text)           # should be a hit
        info_after_second = prompt_version.cache_info()

        assert info_after_second.hits == info_after_first.hits + 1
        assert info_after_second.misses == info_after_first.misses  # no new miss


# ---------------------------------------------------------------------------
# TurnRecord carries prompt_version
# ---------------------------------------------------------------------------

class TestTurnRecordPromptVersion:
    def test_default_is_empty_string(self):
        rec = _record(turn_id="t0")
        assert rec.prompt_version == ""

    def test_stamped_version_survives_record(self):
        pv = prompt_version("my prompt text")
        rec = _record(turn_id="t1", pv=pv)
        assert rec.prompt_version == pv

    def test_asdict_includes_prompt_version(self):
        pv = prompt_version("serialise me")
        rec = _record(turn_id="t2", pv=pv)
        from dataclasses import asdict
        d = asdict(rec)
        assert "prompt_version" in d
        assert d["prompt_version"] == pv

    def test_legacy_records_without_version_still_work(self):
        """Backward-compat: omitting prompt_version defaults to ''."""
        rec = TurnRecord(
            ts=1.0, turn_id="legacy", bot="hive",
            user_msg_preview="old", helpers_used=[],
            total_tokens=10, total_latency_ms=100,
            blocked=False, error=None, actions=[],
            # prompt_version intentionally omitted
        )
        assert rec.prompt_version == ""


# ---------------------------------------------------------------------------
# TurnTelemetry — record + query
# ---------------------------------------------------------------------------

class TestTurnTelemetryQueryByVersion:
    def _build_tel(self) -> TurnTelemetry:
        pv_a = prompt_version("revision A")
        pv_b = prompt_version("revision B")
        tel = TurnTelemetry(max_records=50)
        tel.record(_record("a1", pv=pv_a))
        tel.record(_record("a2", pv=pv_a))
        tel.record(_record("b1", pv=pv_b))
        tel.record(_record("no-ver"))          # no version (legacy)
        return tel, pv_a, pv_b

    def test_query_returns_matching_records(self):
        tel, pv_a, pv_b = self._build_tel()
        results = tel.query_by_prompt_version(pv_a)
        assert [r.turn_id for r in results] == ["a1", "a2"]

    def test_query_other_version(self):
        tel, pv_a, pv_b = self._build_tel()
        results = tel.query_by_prompt_version(pv_b)
        assert [r.turn_id for r in results] == ["b1"]

    def test_query_nonexistent_version_returns_empty(self):
        tel, pv_a, pv_b = self._build_tel()
        results = tel.query_by_prompt_version("000000000000")
        assert results == []

    def test_two_revisions_are_distinguishable(self):
        """The core A/B requirement: two revision tags → disjoint record sets."""
        tel, pv_a, pv_b = self._build_tel()
        set_a = {r.turn_id for r in tel.query_by_prompt_version(pv_a)}
        set_b = {r.turn_id for r in tel.query_by_prompt_version(pv_b)}
        assert set_a and set_b
        assert set_a.isdisjoint(set_b), "A/B sets must not overlap"


class TestTurnTelemetryGroupByVersion:
    def test_group_keys_match_distinct_versions(self):
        pv_a = prompt_version("group-a prompt")
        pv_b = prompt_version("group-b prompt")
        tel = TurnTelemetry()
        tel.record(_record("x1", pv=pv_a))
        tel.record(_record("x2", pv=pv_b))
        tel.record(_record("x3", pv=pv_a))
        tel.record(_record("legacy"))     # pv=""

        groups = tel.group_by_prompt_version()
        assert set(groups.keys()) == {pv_a, pv_b, ""}

    def test_group_counts_are_correct(self):
        pv_a = prompt_version("group-count-a")
        pv_b = prompt_version("group-count-b")
        tel = TurnTelemetry()
        for i in range(5):
            tel.record(_record(f"a{i}", pv=pv_a))
        for i in range(2):
            tel.record(_record(f"b{i}", pv=pv_b))

        groups = tel.group_by_prompt_version()
        assert len(groups[pv_a]) == 5
        assert len(groups[pv_b]) == 2

    def test_group_empty_buffer_returns_empty_dict(self):
        tel = TurnTelemetry()
        assert tel.group_by_prompt_version() == {}
