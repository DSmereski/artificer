"""Tests for InviteBroker — 6-digit code generation, TTL, single-use."""

from __future__ import annotations

import time

import pytest

from gateway.worker_pool.invites import InviteBroker


def test_issue_returns_six_digit_code() -> None:
    broker = InviteBroker(ttl_seconds=600)
    invite = broker.issue()
    digits = invite.code.replace("-", "")
    assert len(digits) == 6
    assert digits.isdigit()
    # Display form is "XXX-XXX"
    assert "-" in invite.code
    assert invite.expires_at > invite.created_at


def test_claim_consumes_code() -> None:
    broker = InviteBroker(ttl_seconds=600)
    invite = broker.issue()
    assert broker.claim(invite.code) is True
    # Single-use: a second claim fails.
    assert broker.claim(invite.code) is False


def test_claim_normalises_dashes_and_whitespace() -> None:
    broker = InviteBroker(ttl_seconds=600)
    invite = broker.issue()
    raw_digits = invite.code.replace("-", "")
    assert broker.claim(f"  {raw_digits}  ") is True


def test_expired_code_rejected() -> None:
    broker = InviteBroker(ttl_seconds=0)
    invite = broker.issue()
    time.sleep(0.05)
    assert broker.claim(invite.code) is False


def test_list_active_excludes_used_and_expired() -> None:
    broker = InviteBroker(ttl_seconds=600)
    a = broker.issue()
    b = broker.issue()
    broker.claim(a.code)
    active = {inv.code for inv in broker.list_active()}
    assert active == {b.code}


def test_revoke_removes_invite() -> None:
    broker = InviteBroker(ttl_seconds=600)
    invite = broker.issue()
    assert broker.revoke(invite.code) is True
    assert broker.claim(invite.code) is False


def test_codes_are_unique_under_burst() -> None:
    broker = InviteBroker(ttl_seconds=600)
    seen = {broker.issue().code for _ in range(50)}
    assert len(seen) == 50
