# gateway/auditor/scanners/composio_scanner.py
"""Flag any saas_call (Composio) action that fired without a prior
critic delegation in the same turn.

The runtime path in `hive_coordinator` already routes risky-verb
actions through the critic before execution, but the auditor is the
post-hoc observability layer: if a code-path bug or missing wiring
ever lets a `saas_call` slip past the critic gate, this scanner
surfaces the leak so it doesn't go unnoticed.

A finding is emitted iff:
  - the turn's synthesis actions OR receipts contain a `saas_call`
    verb, AND
  - the turn's `delegations` list does not contain `critic`.

Severity is HIGH because external SaaS side effects (Slack messages,
GitHub issues, Gmail drafts) cannot be undone by the gateway.
"""
from __future__ import annotations

from gateway.auditor.findings import Finding, Severity


def _has_saas_call(items: list) -> bool:
    for item in items:
        if isinstance(item, dict) and item.get("verb") == "saas_call":
            return True
    return False


class ComposioScanner:
    name = "composio"

    def scan(
        self,
        *,
        turns: list[dict],
        memories: list[dict],
    ) -> list[Finding]:
        out: list[Finding] = []
        for t in turns:
            synth = t.get("synthesis") or {}
            actions = synth.get("actions") if isinstance(synth, dict) else None
            receipts = t.get("receipts")
            saw_action = isinstance(actions, list) and _has_saas_call(actions)
            saw_receipt = isinstance(receipts, list) and _has_saas_call(receipts)
            if not (saw_action or saw_receipt):
                continue
            delegations = t.get("delegations") or []
            delegation_names = {
                str(d).lower() for d in delegations if isinstance(d, str)
            }
            if "critic" in delegation_names:
                continue
            out.append(Finding(
                kind="composio_unconfirmed",
                severity=Severity.HIGH,
                turn_id=str(t.get("turn_id", "?")),
                bot=str(t.get("bot", "")),
                summary="saas_call action fired without critic delegation",
                detail=(
                    f"delegations={sorted(delegation_names)}; "
                    f"actions={'yes' if saw_action else 'no'}; "
                    f"receipts={'yes' if saw_receipt else 'no'}"
                ),
            ))
        return out
