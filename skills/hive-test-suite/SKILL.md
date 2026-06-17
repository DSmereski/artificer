---
name: hive-test-suite
description: Run the Ai-Team / Hive Python test suite (gateway + shared + worker_pool + hive_node_agent). Use when user says "run tests", "test the gateway", "is everything green", or after any code change to the Hive backend.
user_invocable: true
---

# Hive Test Suite

Run the full Python test suite for the Ai-Team / Hive gateway and report pass/fail. Currently 1014 tests, ~150 seconds.

## How to run

```bash
cd "/c/Projects/Ai-Team" && python -m pytest gateway/tests/ shared/tests/ -q
```

Use `run_in_background: true` and a 600000ms (10 min) timeout. The full suite needs ~150s; pytest-asyncio is noisy with deprecation warnings (53k+ at last count) — that's normal, ignore them.

## Subset commands

| Need | Command |
|---|---|
| One file | `python -m pytest gateway/tests/test_<name>.py -q` |
| One test | `python -m pytest gateway/tests/test_<name>.py::test_<func> -xvs` |
| Just shared | `python -m pytest shared/tests/ -q` |
| Just node-installer | `python -m pytest gateway/worker_pool/tests/ hive_node_agent/tests/ -q` |
| Stop on first fail | add `-x` |
| With output | add `-s` |

## What to report back

After the run, report:
- pass / fail count
- name and file:line of each failure
- runtime (sanity check ~150s; if much longer, something's hung)

If failures involve `RuntimeError: Event loop is closed` warnings only (not actual test failures), those are pytest-asyncio teardown noise on Python 3.14 — ignore them. If they appear inside a `FAILED` test, then they matter.

## Known-good baseline

As of 2026-05-10: **1014 passed in ~150s**. If your run shows 1013 or fewer, find what broke.

## When NOT to use this skill

- For one-off test runs against a specific file → just call pytest directly with the path.
- For Flutter / Dart tests → that's a separate stack (`flutter test` under `lib/` parent).
