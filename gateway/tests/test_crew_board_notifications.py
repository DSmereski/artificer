"""CrewNotifier — verify board events also reach the main EventBus so
the app's /v1/events listener can notify + deep-link."""

from __future__ import annotations

from gateway.crew_board.notifications import CrewNotifier


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, event: dict) -> None:
        self.published.append(event)


def test_broadcast_mirrors_to_event_bus() -> None:
    bus = _FakeBus()
    n = CrewNotifier(event_bus=bus)  # no subscribers, no ntfy
    n.broadcast({"event": "review_ready", "task": "T-9"})
    assert bus.published == [
        {"type": "board_event", "event": "review_ready", "task": "T-9"}
    ]


def test_broadcast_without_bus_is_noop() -> None:
    n = CrewNotifier()  # event_bus defaults to None
    # Must not raise when no bus / subscribers / ntfy are configured.
    n.broadcast({"event": "task_moved", "task": "T-1", "status": "review"})


def test_bus_publish_failure_is_swallowed() -> None:
    class _Boom:
        def publish(self, event: dict) -> None:
            raise RuntimeError("bus down")

    n = CrewNotifier(event_bus=_Boom())
    # Best-effort — a bus failure must not break the dispatcher.
    n.broadcast({"event": "escalated", "task": "T-2"})
