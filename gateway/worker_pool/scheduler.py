"""Capability-match scheduler.

Given a polling node's reported capabilities (runtimes available + free
VRAM), pick the oldest queued job whose `required_caps` are satisfied
and atomically mark it dispatched. Pure-Python; the only state lives in
the Dispatcher's SQLite file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gateway.worker_pool.dispatcher import Dispatcher, HiveJob


_VRAM_PATTERN = re.compile(r"^vram_mb\s*>=\s*(\d+)$")


@dataclass(frozen=True, slots=True)
class NodeView:
    """Snapshot the scheduler needs to match a job to a node.

    Built by the route handler from `require_node()` + the latest
    capability snapshot the node sent in the poll query string.
    """
    node_id: str
    caps: set[str]
    vram_free_mb: int


class Scheduler:
    def __init__(self, *, dispatcher: Dispatcher) -> None:
        self._disp = dispatcher

    def pick_for_node(self, node: NodeView) -> HiveJob | None:
        """Return the oldest queued job this node can satisfy, having
        atomically transitioned it to `dispatched`. None if nothing
        matches or another node took it under race."""
        for candidate in self._disp.get_queued():
            if not self._matches(candidate.required_caps, node):
                continue
            if self._disp.assign_to_node(candidate.id, node_id=node.node_id):
                # Re-fetch so the caller sees status=dispatched + attempts++.
                return self._disp.get(candidate.id) or candidate
            # Lost the race; keep scanning.
            continue
        return None

    @staticmethod
    def _matches(required_caps: tuple[str, ...], node: NodeView) -> bool:
        for cap in required_caps:
            m = _VRAM_PATTERN.match(cap)
            if m:
                if node.vram_free_mb < int(m.group(1)):
                    return False
                continue
            if cap not in node.caps:
                return False
        return True
