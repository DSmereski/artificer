"""Tests for `shared.audience.clamp_audience`.

These pin the security-relevant invariants the 2026-04-29 review
called out:
  - a narrower device CAN'T write a wider audience
  - the privileged `all` device leaves the request alone
  - empty / None device defaults to unclamped (legacy callers)
  - empty intersection falls back to the device's audience (so the
    write isn't silently dropped)
"""

from __future__ import annotations

from shared.audience import clamp_audience


def test_no_device_audience_returns_request_unchanged():
    assert clamp_audience(["hive"], None) == ["hive"]
    assert clamp_audience(["hive", "claude-code"], []) == ["hive", "claude-code"]


def test_all_device_is_privileged_passthrough():
    assert clamp_audience(["hive"], ["all"]) == ["hive"]
    assert clamp_audience(["claude-code"], ["all", "hive"]) == ["claude-code"]


def test_narrow_device_intersects_request():
    """A `[hive]` device CAN'T write `[all]` or `[claude-code]`."""
    assert clamp_audience(["all"], ["hive"]) == ["hive"]
    assert clamp_audience(["claude-code"], ["hive"]) == ["hive"]
    assert clamp_audience(["hive", "claude-code"], ["hive"]) == ["hive"]


def test_empty_intersection_falls_back_to_device():
    """Without the fallback the write would silently land at empty
    audience and be invisible to everyone."""
    assert clamp_audience([], ["hive"]) == ["hive"]
    assert clamp_audience(["claude-code"], ["hive"]) == ["hive"]


def test_request_subset_of_device_returns_subset():
    """When the request is already inside the device's audience, it
    passes through unmodified."""
    assert clamp_audience(["hive"], ["hive", "claude-code"]) == ["hive"]


def test_returns_fresh_list():
    """Caller-side mutation must NOT touch the inputs."""
    req = ["hive"]
    dev = ["hive", "claude-code"]
    out = clamp_audience(req, dev)
    out.append("evil")
    assert req == ["hive"]
    assert dev == ["hive", "claude-code"]


def test_accepts_arbitrary_iterables():
    """Tuples + sets are common in calling code (Device.audience is a
    tuple). The clamp must not assume list inputs."""
    assert clamp_audience(("hive",), ("all",)) == ["hive"]
    assert clamp_audience({"hive"}, ("hive", "claude-code")) == ["hive"]
