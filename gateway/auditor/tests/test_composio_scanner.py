"""Tests for the Composio scanner.

Reads turn-log dicts shaped like the live `TurnLogStore.to_jsonable`
output: `synthesis.actions`, top-level `receipts`, top-level
`delegations`. A finding fires only when a `saas_call` exists AND the
critic isn't in delegations.
"""

from __future__ import annotations

from gateway.auditor.findings import Severity
from gateway.auditor.scanners.composio_scanner import ComposioScanner


def _turn(**overrides) -> dict:
    base = {
        "turn_id": "t1",
        "bot": "hive",
        "delegations": [],
        "synthesis": {"actions": []},
        "receipts": [],
    }
    base.update(overrides)
    return base


def test_no_findings_when_no_saas_call():
    s = ComposioScanner()
    findings = s.scan(turns=[_turn()], memories=[])
    assert findings == []


def test_no_finding_when_critic_delegated():
    s = ComposioScanner()
    t = _turn(
        delegations=["planner", "critic"],
        synthesis={"actions": [{"verb": "saas_call",
                                "payload": {"app": "slack", "action": "post"}}]},
    )
    findings = s.scan(turns=[t], memories=[])
    assert findings == []


def test_finding_when_saas_action_without_critic():
    s = ComposioScanner()
    t = _turn(
        delegations=["planner"],
        synthesis={"actions": [{"verb": "saas_call",
                                "payload": {"app": "slack"}}]},
    )
    [f] = s.scan(turns=[t], memories=[])
    assert f.kind == "composio_unconfirmed"
    assert f.severity == Severity.HIGH
    assert "without critic" in f.summary


def test_finding_fires_on_receipt_path_too():
    """If somehow only a receipt records the verb (and not the action
    list), the scanner should still flag — receipts are the executor's
    output and prove the call actually fired."""
    s = ComposioScanner()
    t = _turn(
        delegations=["planner"],
        synthesis={"actions": []},
        receipts=[{"verb": "saas_call", "ok": True}],
    )
    [f] = s.scan(turns=[t], memories=[])
    assert f.kind == "composio_unconfirmed"


def test_critic_match_is_case_insensitive():
    s = ComposioScanner()
    t = _turn(
        delegations=["Planner", "Critic"],
        synthesis={"actions": [{"verb": "saas_call"}]},
    )
    findings = s.scan(turns=[t], memories=[])
    assert findings == []


def test_other_verbs_dont_trigger():
    s = ComposioScanner()
    t = _turn(
        delegations=["planner"],
        synthesis={"actions": [{"verb": "vault_learn"}]},
    )
    findings = s.scan(turns=[t], memories=[])
    assert findings == []


def test_malformed_synthesis_block_safe():
    s = ComposioScanner()
    t = _turn(synthesis="not-a-dict", receipts=[])
    findings = s.scan(turns=[t], memories=[])
    assert findings == []


def test_default_scanners_includes_composio():
    from gateway.auditor.audit_run import default_scanners
    names = [s.name for s in default_scanners()]
    assert "composio" in names
