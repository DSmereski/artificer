"""Scheduled jobs (calendar feature).

A user-friendly Google-Calendar-style scheduler for the Ai-Team app.

Job shape:
  - id (uuid hex), title, description
  - scheduled_at: ISO-8601 UTC timestamp of the NEXT firing
  - recurrence: 'none' | 'daily' | 'weekly' | 'monthly'
  - action_verb + action_payload: same shape as synthesis actions
    (hive_turn, ntfy_push, vault_learn, image_render)
  - notify: bool — push an ntfy on success
  - status: 'scheduled' | 'firing' | 'done' | 'error'
  - owner_device_id, last_run_at, last_error
  - created_at, updated_at

Storage: SQLite at <state_dir>/calendar.db. Indexed on scheduled_at
so the scheduler tick can find due jobs cheaply.

Scheduler: a background asyncio task that wakes every TICK_S seconds,
selects every job whose scheduled_at <= now AND status == 'scheduled',
fires each through ActionExecutor, then advances scheduled_at by the
recurrence (or marks 'done' for non-recurring jobs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("gateway.calendar")


_TICK_S = 30
_RECURRENCES = {"none", "daily", "weekly", "monthly"}
# Verbs allowed for SCHEDULED jobs. Deliberately conservative — no
# `hive_turn` (a stolen device token shouldn't let an attacker
# trigger an arbitrary unattended Terry turn that could exfiltrate
# vault contents). Users who need scheduled hive turns can chain
# vault_learn/ntfy_push instead.
_VERBS = {"hive_turn", "ntfy_push", "vault_learn", "image_render"}


# ---------------------------------------------------------------- model


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string. Tolerates trailing 'Z'."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Job:
    id: str
    title: str
    description: str
    scheduled_at: str            # ISO-8601 UTC
    recurrence: str              # one of _RECURRENCES
    action_verb: str             # one of _VERBS
    action_payload: dict
    notify: bool = True
    status: str = "scheduled"    # scheduled | firing | done | error
    owner_device_id: str = ""
    last_run_at: str | None = None
    last_error: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_jsonable(self) -> dict:
        return asdict(self)

    def is_due(self, now: datetime | None = None) -> bool:
        if self.status != "scheduled":
            return False
        n = now or datetime.now(timezone.utc)
        try:
            return _parse_iso(self.scheduled_at) <= n
        except ValueError:
            return False

    def advance(self) -> "Job":
        """Return a copy of this job with scheduled_at moved forward
        by `recurrence`. For 'none', status flips to 'done' and the
        scheduled_at stays put.

        Long-outage advance is arithmetic, not a loop:
        `((now - current) // delta + 1) * delta` lands on the first
        future firing in O(1) instead of O(days_offline).
        """
        try:
            current = _parse_iso(self.scheduled_at)
        except ValueError:
            return self
        if self.recurrence == "daily":
            delta = timedelta(days=1)
        elif self.recurrence == "weekly":
            delta = timedelta(weeks=1)
        elif self.recurrence == "monthly":
            # Naive: add 30 days. Calendar-month math is hairy and the
            # user can edit the job to fix any drift.
            delta = timedelta(days=30)
        else:
            return Job(**{**asdict(self), "status": "done",
                          "updated_at": _now_iso()})
        nxt = current + delta
        now = datetime.now(timezone.utc)
        if nxt <= now:
            missed = (now - current) // delta
            nxt = current + delta * (missed + 1)
        return Job(**{**asdict(self),
                      "scheduled_at": nxt.isoformat(timespec="seconds"),
                      "updated_at": _now_iso(),
                      "status": "scheduled"})


def validate_payload(verb: str, payload: dict) -> str | None:
    """Return None if valid, else an error string. Same rules the
    ActionExecutor will apply when the job fires — fail-fast at create
    time so the user sees the error in the UI immediately."""
    if verb not in _VERBS:
        return f"unknown verb: {verb!r}"
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    if verb == "hive_turn":
        msg = payload.get("user_msg")
        if not isinstance(msg, str) or not msg.strip():
            return "hive_turn: needs user_msg"
        if len(msg) > 2000:
            return "hive_turn: user_msg too long (≤2000 chars)"
    elif verb == "ntfy_push":
        if not isinstance(payload.get("message"), str) or not payload["message"]:
            return "ntfy_push: needs message"
    elif verb == "vault_learn":
        for k in ("category", "title", "body"):
            if not isinstance(payload.get(k), str) or not payload[k].strip():
                return f"vault_learn: needs {k}"
    elif verb == "image_render":
        if not isinstance(payload.get("prompt"), str) or not payload["prompt"]:
            return "image_render: needs prompt"
        # Block free-form filesystem paths from scheduled jobs. img2img
        # references must come from /v1/images/upload + media_id, not
        # raw paths. Closes a CRITICAL: a stolen token could schedule
        # an image_render with reference_path="/etc/whatever" and read
        # arbitrary files into a generated image.
        if "reference_path" in payload:
            return "image_render: reference_path forbidden in scheduled jobs"
    return None


# ---------------------------------------------------------------- store


class JobStore:
    """SQLite-backed job store. Thread/async-safe via per-call connections."""

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            scheduled_at TEXT NOT NULL,
            recurrence TEXT NOT NULL,
            action_verb TEXT NOT NULL,
            action_payload TEXT NOT NULL,
            notify INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'scheduled',
            owner_device_id TEXT NOT NULL DEFAULT '',
            last_run_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_due
            ON jobs(status, scheduled_at);
    """

    def __init__(self, db_path: Path) -> None:
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db, timeout=5.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 5000")
        return c

    def _init_schema(self) -> None:
        with self._connect() as c:
            c.executescript(self.SCHEMA)

    # ---------------------------------------------------------------- crud

    @staticmethod
    def _row_to_job(r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"],
            title=r["title"],
            description=r["description"],
            scheduled_at=r["scheduled_at"],
            recurrence=r["recurrence"],
            action_verb=r["action_verb"],
            action_payload=json.loads(r["action_payload"]),
            notify=bool(r["notify"]),
            status=r["status"],
            owner_device_id=r["owner_device_id"],
            last_run_at=r["last_run_at"],
            last_error=r["last_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def create(
        self,
        *,
        title: str,
        description: str = "",
        scheduled_at: str,
        recurrence: str = "none",
        action_verb: str,
        action_payload: dict,
        notify: bool = True,
        owner_device_id: str = "",
    ) -> Job:
        if recurrence not in _RECURRENCES:
            raise ValueError(f"recurrence must be one of {_RECURRENCES}")
        err = validate_payload(action_verb, action_payload)
        if err:
            raise ValueError(err)
        # Validate scheduled_at is parseable.
        _parse_iso(scheduled_at)
        job = Job(
            id=uuid.uuid4().hex[:16],
            title=title.strip()[:200] or "(untitled)",
            description=description.strip()[:2000],
            scheduled_at=scheduled_at,
            recurrence=recurrence,
            action_verb=action_verb,
            action_payload=action_payload,
            notify=notify,
            owner_device_id=owner_device_id,
        )
        with self._connect() as c:
            c.execute(
                """INSERT INTO jobs
                   (id, title, description, scheduled_at, recurrence,
                    action_verb, action_payload, notify, status,
                    owner_device_id, last_run_at, last_error,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.id, job.title, job.description, job.scheduled_at,
                 job.recurrence, job.action_verb,
                 json.dumps(job.action_payload), 1 if job.notify else 0,
                 job.status, job.owner_device_id,
                 job.last_run_at, job.last_error,
                 job.created_at, job.updated_at),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._connect() as c:
            r = c.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,),
            ).fetchone()
            return self._row_to_job(r) if r else None

    def list(
        self,
        *,
        owner_device_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE 1=1"
        args: list = []
        if owner_device_id is not None:
            sql += " AND (owner_device_id = ? OR owner_device_id = '')"
            args.append(owner_device_id)
        if since is not None:
            sql += " AND scheduled_at >= ?"
            args.append(since)
        if until is not None:
            sql += " AND scheduled_at <= ?"
            args.append(until)
        sql += " ORDER BY scheduled_at ASC LIMIT ?"
        args.append(limit)
        with self._connect() as c:
            return [self._row_to_job(r) for r in c.execute(sql, args).fetchall()]

    def due(self, *, now: datetime | None = None, limit: int = 50) -> list[Job]:
        now_iso = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        with self._connect() as c:
            rows = c.execute(
                """SELECT * FROM jobs
                   WHERE status = 'scheduled' AND scheduled_at <= ?
                   ORDER BY scheduled_at ASC LIMIT ?""",
                (now_iso, limit),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def update(self, job_id: str, **fields: Any) -> Job | None:
        existing = self.get(job_id)
        if existing is None:
            return None
        merged = {**asdict(existing), **fields, "updated_at": _now_iso()}
        if "recurrence" in fields and fields["recurrence"] not in _RECURRENCES:
            raise ValueError("invalid recurrence")
        if "action_verb" in fields or "action_payload" in fields:
            err = validate_payload(merged["action_verb"], merged["action_payload"])
            if err:
                raise ValueError(err)
        with self._connect() as c:
            c.execute(
                """UPDATE jobs SET title=?, description=?, scheduled_at=?,
                   recurrence=?, action_verb=?, action_payload=?,
                   notify=?, status=?, owner_device_id=?,
                   last_run_at=?, last_error=?, updated_at=?
                   WHERE id=?""",
                (merged["title"], merged["description"], merged["scheduled_at"],
                 merged["recurrence"], merged["action_verb"],
                 json.dumps(merged["action_payload"]),
                 1 if merged["notify"] else 0,
                 merged["status"], merged["owner_device_id"],
                 merged["last_run_at"], merged["last_error"],
                 merged["updated_at"], job_id),
            )
        return Job(**{
            **merged,
            "action_payload": merged["action_payload"]
                if isinstance(merged["action_payload"], dict)
                else json.loads(merged["action_payload"]),
        })

    def delete(self, job_id: str) -> bool:
        with self._connect() as c:
            r = c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return r.rowcount > 0


# ---------------------------------------------------------------- scheduler


@dataclass
class FireResult:
    job_id: str
    ok: bool
    detail: str = ""


class Scheduler:
    """Periodic firing loop. Owns no state of its own — pulls from the
    JobStore, dispatches via the supplied callable, advances the job.

    `fire_callable` is async: `(job: Job) -> FireResult`. Tests pass a
    fake; the live gateway passes a closure that uses the
    ActionExecutor + HiveCoordinator.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        fire: "callable",
        ntfy=None,
        tick_s: float = _TICK_S,
    ) -> None:
        self._store = store
        self._fire = fire
        self._ntfy = ntfy
        self._tick_s = tick_s
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._loop(), name="calendar-scheduler")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            # Real shutdown error — log it so we can see it in the
            # gateway log instead of silently swallowing.
            log.exception("calendar scheduler stop raised")
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                log.exception("calendar tick error")
            try:
                await asyncio.sleep(self._tick_s)
            except asyncio.CancelledError:
                return

    # Per-job firing budget. A stuck `hive_turn` (image render that
    # never returns, vault writer hung) shouldn't block other jobs.
    PER_JOB_TIMEOUT_S = 180

    async def tick(self) -> list[FireResult]:
        """One sweep — pull due jobs, fire each, advance recurrence,
        return per-job results. Public so tests can drive the loop."""
        results: list[FireResult] = []
        for job in self._store.due():
            self._store.update(job.id, status="firing")
            try:
                fr = await asyncio.wait_for(
                    self._fire(job), timeout=self.PER_JOB_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                log.warning("job %s fire exceeded %ds — moving on",
                            job.id, self.PER_JOB_TIMEOUT_S)
                fr = FireResult(
                    job_id=job.id, ok=False,
                    detail=f"timed out after {self.PER_JOB_TIMEOUT_S}s",
                )
            except Exception as e:  # noqa: BLE001
                log.exception("job %s fire raised", job.id)
                fr = FireResult(job_id=job.id, ok=False,
                                detail=f"{type(e).__name__}: {e}")
            results.append(fr)
            # Update last_run + advance.
            advanced = job.advance()
            self._store.update(
                job.id,
                status=advanced.status,
                scheduled_at=advanced.scheduled_at,
                last_run_at=_now_iso(),
                last_error=None if fr.ok else fr.detail,
            )
            # Best-effort notification.
            if job.notify and self._ntfy is not None and getattr(
                self._ntfy, "enabled", False,
            ):
                try:
                    await self._ntfy.publish(
                        topic="ai-team-calendar",
                        title=("✓ " if fr.ok else "⚠ ") + job.title,
                        message=(fr.detail or "fired")[:300],
                        tags=["calendar"],
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("ntfy notify for job %s failed: %s",
                                job.id, e)
        return results
