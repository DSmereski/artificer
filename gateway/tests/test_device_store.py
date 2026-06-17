"""Unit tests for `gateway.auth.DeviceStore` invariants that aren't
already covered by the `/v1/pair` route tests in test_auth.py.

The headline invariant pinned here:
  `list_active()` must NEVER return revoked devices. Callers in
  routes/chat.py use it to look up the audience tuple before clamping
  vault writes — if a revoked device leaks through, an old phone's
  stale token can colour the audience of a fresh session.
"""

from __future__ import annotations

from gateway.auth import DeviceStore


def test_list_active_excludes_revoked(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    a = store.add(name="phone", token="tok-a")
    b = store.add(name="laptop", token="tok-b")

    assert {d.id for d in store.list_active()} == {a.id, b.id}

    store.revoke(a.id)

    active = store.list_active()
    assert {d.id for d in active} == {b.id}, (
        "revoked device leaked into list_active() — audience clamp "
        "lookup will read the wrong audience tuple"
    )
    # `list()` keeps everything (including revoked) for audit.
    assert {d.id for d in store.list()} == {a.id, b.id}


def test_list_active_after_purge_is_clean(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    a = store.add(name="phone", token="tok-a")
    b = store.add(name="laptop", token="tok-b")
    store.revoke(a.id)
    store.purge_revoked()
    active = store.list_active()
    assert {d.id for d in active} == {b.id}
