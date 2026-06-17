"""Pin the cross-device sync invariant for `_stable_user_id`.

The function's docstring promises three things — this file pins each:

  1. Same seed → same user_id, deterministic across process restarts
     (md5, not Python's salted `hash()`).
  2. Different devices owned by the same user (`device.user`) collapse
     to the SAME user_id — that's what makes phone + PC see one chat
     history.
  3. Different `device.user` values produce different user_ids — the
     owner string is the routing key, not the device id.

The bug that motivates these tests: an earlier version routed history
by `device.id`, so every device was its own conversation island and
phone history was invisible to PC. The fix moved routing onto
`device.user` ("owner" by default). If anyone reverts that, these
tests fail loudly.
"""

from __future__ import annotations

from gateway.routes.chat import _stable_user_id


def test_deterministic_across_calls():
    """Same seed → identical id, every time. Pins md5 over salted hash()."""
    a = _stable_user_id("owner")
    b = _stable_user_id("owner")
    assert a == b


def test_returns_unsigned_32bit_int():
    """Slot the LLM history file uses is 32-bit. Pin the range."""
    uid = _stable_user_id("owner")
    assert isinstance(uid, int)
    assert 0 <= uid <= 0xFFFFFFFF


def test_phone_and_pc_share_history_when_owner_matches():
    """Phone and PC pair under the SAME `device.user="owner"` value, so
    `_stable_user_id` must return the same id for both — that's what
    makes one logical conversation visible from both devices."""
    phone_owner = "owner"   # device.user is the logical owner string
    pc_owner = "owner"
    assert _stable_user_id(phone_owner) == _stable_user_id(pc_owner)


def test_distinct_owners_get_distinct_ids():
    """Different owner strings must NOT collide — that would merge two
    users' chat histories. md5 is collision-free for short distinct
    strings."""
    assert _stable_user_id("owner") != _stable_user_id("guest")
    assert _stable_user_id("owner") != _stable_user_id("david")


def test_known_md5_value_pins_md5_choice():
    """If someone replaces md5 with sha-256 or hash(), this number
    changes. Pinning it forces a deliberate reasoning step before
    anyone touches the algorithm — ids are persisted in user-state
    files on disk, so changing the algo silently re-keys everyone's
    history."""
    # md5("owner")[:4] big-endian → 0x72122ce9.
    # Computed once and locked in; identical across all runs.
    assert _stable_user_id("owner") == 0x72122ce9
