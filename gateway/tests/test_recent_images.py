"""Unit tests for gateway.recent_images.RecentImagesStore."""

from __future__ import annotations

import time

from gateway.recent_images import RecentImagesStore


def test_record_and_recent_returns_for_device():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="j1", prompt="cat")
    out = store.recent(device_id="d1")
    assert len(out) == 1 and out[0].job_id == "j1"
    assert out[0].state == "running"


def test_recent_isolates_devices():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="j1", prompt="cat")
    store.record(device_id="d2", bot="hive", job_id="j2", prompt="dog")
    assert [j.job_id for j in store.recent(device_id="d1")] == ["j1"]
    assert [j.job_id for j in store.recent(device_id="d2")] == ["j2"]


def test_update_completion_attaches_media_id():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="j1", prompt="cat")
    store.update_completion(job_id="j1", state="done", result_ids=["m1"])
    out = store.recent(device_id="d1")
    assert out[0].state == "done"
    assert out[0].result_ids == ["m1"]


def test_update_completion_records_error():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="j1", prompt="cat")
    store.update_completion(job_id="j1", state="error", error="oom")
    out = store.recent(device_id="d1")
    assert out[0].state == "error" and out[0].error == "oom"


def test_update_completion_unknown_job_is_silent():
    store = RecentImagesStore()
    # Should not raise.
    store.update_completion(job_id="ghost", state="done", result_ids=["m1"])


def test_recent_filters_by_since_ts():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="old", prompt="x")
    time.sleep(0.01)
    cutoff = time.time()
    time.sleep(0.01)
    store.record(device_id="d1", bot="hive", job_id="new", prompt="y")
    fresh = store.recent(device_id="d1", since_ts=cutoff)
    assert [j.job_id for j in fresh] == ["new"]


def test_recent_filters_by_bot():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="t1", prompt="x")
    store.record(device_id="d1", bot="maggy", job_id="m1", prompt="y")
    out = store.recent(device_id="d1", bot="hive")
    assert [j.job_id for j in out] == ["t1"]


def test_recent_returns_newest_first():
    store = RecentImagesStore()
    store.record(device_id="d1", bot="hive", job_id="a", prompt="x")
    time.sleep(0.01)
    store.record(device_id="d1", bot="hive", job_id="b", prompt="y")
    time.sleep(0.01)
    store.record(device_id="d1", bot="hive", job_id="c", prompt="z")
    out = store.recent(device_id="d1")
    assert [j.job_id for j in out] == ["c", "b", "a"]


def test_per_device_cap_evicts_oldest():
    store = RecentImagesStore(max_per_device=3)
    for i in range(5):
        store.record(device_id="d1", bot="hive", job_id=f"j{i}", prompt=str(i))
    out = store.recent(device_id="d1")
    ids = sorted(j.job_id for j in out)
    # Oldest (j0, j1) evicted, newest 3 (j2, j3, j4) survive.
    assert ids == ["j2", "j3", "j4"]


def test_retention_drops_expired_jobs():
    store = RecentImagesStore(retention_seconds=0.05)
    store.record(device_id="d1", bot="hive", job_id="old", prompt="x")
    time.sleep(0.1)
    store.record(device_id="d1", bot="hive", job_id="new", prompt="y")
    out = store.recent(device_id="d1")
    assert [j.job_id for j in out] == ["new"]
