"""Tests for the M4.2 source-cited REMEMBER payload."""

from __future__ import annotations

import json

from gateway.conversation_markers import parse_remember


def _payload(**kwargs):
    base = {"category": "knowledge", "title": "x", "body": "b"}
    base.update(kwargs)
    return json.dumps(base)


def test_no_sources_no_extra():
    out = parse_remember(_payload())
    assert "extra" not in out


def test_sources_land_in_extra():
    out = parse_remember(_payload(sources=[
        {"url": "https://a.example.com", "title": "A", "accessed": "2026-04-26T12:00:00Z"},
        {"url": "https://b.example.com", "title": "B"},
    ]))
    assert "extra" in out
    sources = out["extra"]["sources"]
    assert len(sources) == 2
    assert sources[0]["url"] == "https://a.example.com"
    assert sources[0]["title"] == "A"
    assert sources[1]["url"] == "https://b.example.com"


def test_sources_drop_malformed_entries():
    out = parse_remember(_payload(sources=[
        {"url": "https://ok.example.com"},
        "not a dict",
        {"title": "no url"},
        {"url": ""},
    ]))
    sources = out["extra"]["sources"]
    assert len(sources) == 1
    assert sources[0]["url"] == "https://ok.example.com"


def test_sources_capped_at_10():
    out = parse_remember(_payload(sources=[
        {"url": f"https://{i}.example.com"} for i in range(20)
    ]))
    assert len(out["extra"]["sources"]) == 10


def test_corroboration_int_passes_through():
    out = parse_remember(_payload(
        sources=[{"url": "https://a.example.com"}],
        corroboration=2,
    ))
    assert out["extra"]["corroboration"] == 2


def test_corroboration_invalid_dropped():
    out = parse_remember(_payload(
        sources=[{"url": "https://a.example.com"}],
        corroboration="lots",
    ))
    assert "corroboration" not in out["extra"]


def test_url_too_long_dropped():
    out = parse_remember(_payload(sources=[
        {"url": "https://" + "x" * 1100 + ".com"},
    ]))
    assert "extra" not in out  # nothing valid → no extra block
