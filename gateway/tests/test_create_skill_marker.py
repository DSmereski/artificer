"""Tests for [CREATE_SKILL] marker scanning."""

from __future__ import annotations

import json

from gateway.conversation_markers import parse_create_skill, scan, strip_markers


_GOOD_BODY = (
    "---\n"
    "name: smoke\n"
    "description: a test skill, with enough body chars.\n"
    "audience: [hive]\n"
    "---\n\n"
    "# Smoke\n\n"
    "1. First step.\n"
    "2. Second step.\n"
    "3. Third step.\n"
)


def test_parse_create_skill_happy():
    out = parse_create_skill(json.dumps({
        "name": "smoke", "body": _GOOD_BODY,
    }))
    assert out is not None
    assert out["name"] == "smoke"
    assert "First step" in out["body"]


def test_parse_create_skill_short_body_rejected():
    out = parse_create_skill(json.dumps({"name": "x", "body": "tiny"}))
    assert out is None


def test_parse_create_skill_missing_frontmatter_rejected():
    out = parse_create_skill(json.dumps({
        "name": "x", "body": "## just a heading and " + ("y" * 200),
    }))
    assert out is None


def test_parse_create_skill_long_name_rejected():
    out = parse_create_skill(json.dumps({
        "name": "x" * 100, "body": _GOOD_BODY,
    }))
    assert out is None


def test_parse_create_skill_malformed_json():
    assert parse_create_skill("not json") is None
    assert parse_create_skill('{"x":') is None


def test_scan_picks_up_create_skill():
    raw = "[CREATE_SKILL] " + json.dumps({"name": "smoke", "body": _GOOD_BODY})
    hits = scan(raw)
    assert hits.create_skill is not None
    assert hits.create_skill["name"] == "smoke"


def test_strip_markers_removes_create_skill_block():
    raw = (
        "Here's a new skill:\n"
        "[CREATE_SKILL] " + json.dumps({"name": "x", "body": _GOOD_BODY}) + "\n"
        "It'll be ready when the critic OKs it."
    )
    out = strip_markers(raw)
    assert "Here's a new skill" in out
    assert "ready when the critic" in out
    assert "CREATE_SKILL" not in out
