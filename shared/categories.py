"""Single source of truth for vault category → folder mapping.

Both the vault_writer daemon (where notes get persisted to disk) and the
gateway action_executor (where [REMEMBER] payloads from the synthesizer
are validated before being forwarded) need this mapping. Keeping two
copies invited drift — and a real bug surfaced when the daemon accepted
a `"canon"` category that the executor's local copy did not, raising a
silent KeyError at the executor before the request ever reached the
daemon.
"""

from __future__ import annotations

# Category -> top-level folder under the vault root.
#
# `canon` is reachable here because the daemon's resolver still uses it
# (and rejects writes targeting it as part of its policy). The gateway
# action executor independently rejects "canon" before this map is
# consulted, so including the key is harmless on the gateway side and
# matches the daemon's view.
CATEGORY_FOLDER: dict[str, str] = {
    "canon":     "canon",       # human-only — daemon rejects writes
    "knowledge": "knowledge",
    "system":    "system",
    "project":   "projects",
    "tool":      "tools",
    "ops":       "ops",
    "person":    "people",
    "journal":   "journals",
    "session":   "sessions",
    "reference": "references",
}
