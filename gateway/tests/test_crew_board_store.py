"""CrewBoardStore unit tests.

Cover: schema apply, project upsert, task lifecycle, state-machine
gating, assignment, attempts, comments, approvals. Each test uses a
fresh tmp DB so state never leaks.
"""

from __future__ import annotations

import pytest

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project, Task


@pytest.fixture
def store(tmp_path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew.db")


# ---------------------------------------------------------------- schema


def test_schema_apply_is_idempotent(tmp_path):
    """Re-opening the same DB must not error or duplicate rows."""
    db = tmp_path / "crew.db"
    s1 = CrewBoardStore(db)
    s1.close()
    s2 = CrewBoardStore(db)
    assert s2.list_projects() == []
    s2.close()


# ---------------------------------------------------------------- projects


def test_upsert_and_list_projects(store):
    p = store.upsert_project(Project(
        slug="example-project", path="./example-project",
        name="ExampleProject",
    ))
    assert p.slug == "example-project"
    assert p.enabled is False
    listed = store.list_projects()
    assert len(listed) == 1
    assert listed[0].name == "ExampleProject"


def test_set_project_enabled(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    store.set_project_enabled("fg", enabled=True)
    p = store.get_project("fg")
    assert p is not None and p.enabled is True
    assert store.list_projects(enabled_only=True) == [p]


# ---------------------------------------------------------------- tasks


def test_create_task_owner_lands_in_backlog(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(
        title="Refactor auth", project_slug="fg",
        created_by="owner",
    )
    assert t.status == schema.STATUS_BACKLOG
    assert t.slug.startswith("T-")


def test_create_task_bot_lands_in_proposed(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(
        title="Bot idea", project_slug="fg", created_by="hive",
    )
    assert t.status == schema.STATUS_PROPOSED


def test_create_task_assigns_monotonic_slug(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t1 = store.create_task(title="a", project_slug="fg")
    t2 = store.create_task(title="b", project_slug="fg")
    assert t1.slug == "T-0001"
    assert t2.slug == "T-0002"


def test_create_task_rejects_unknown_priority(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    with pytest.raises(ValueError):
        store.create_task(title="x", project_slug="fg", priority="urgent")


def test_create_task_persists_complex_fields(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(
        title="x", project_slug="fg",
        acceptance_criteria=[
            {"text": "tests pass", "checked": False},
            {"text": "no console errors", "checked": False},
        ],
        files_of_interest=["src/auth/**/*.ts"],
        depends_on=["T-0001"],
        tags=["security", "refactor"],
    )
    t2 = store.get_task(t.slug)
    assert t2 is not None
    assert len(t2.acceptance_criteria) == 2
    assert t2.files_of_interest == ["src/auth/**/*.ts"]
    assert t2.depends_on == ["T-0001"]
    assert "security" in t2.tags


# ---------------------------------------------------------------- moves


def test_move_task_through_full_flow(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(
        title="x", project_slug="fg", created_by="hive",
    )
    assert t.status == schema.STATUS_PROPOSED
    t = store.move_task(t.slug, schema.STATUS_BACKLOG)
    assert t.status == schema.STATUS_BACKLOG
    t = store.move_task(t.slug, schema.STATUS_READY)
    t = store.move_task(t.slug, schema.STATUS_IN_PROGRESS)
    t = store.move_task(t.slug, schema.STATUS_REVIEW)
    t = store.move_task(t.slug, schema.STATUS_DONE)
    assert t.status == schema.STATUS_DONE


def test_move_task_rejects_invalid_transition(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    # backlog -> in_progress is illegal (must pass through ready)
    with pytest.raises(ValueError):
        store.move_task(t.slug, schema.STATUS_IN_PROGRESS)


def test_move_task_can_step_back(store):
    """Allow backlog -> proposed (owner demotes a bot promotion)."""
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(
        title="x", project_slug="fg", created_by="hive",
    )
    t = store.move_task(t.slug, schema.STATUS_BACKLOG)
    t = store.move_task(t.slug, schema.STATUS_PROPOSED)
    assert t.status == schema.STATUS_PROPOSED


# ---------------------------------------------------------------- assignment


def test_assign_task(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    assert t.assignee == "none"
    t = store.assign_task(t.slug, "hive")
    assert t.assignee == "hive"


def test_assign_rejects_unknown_assignee(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    with pytest.raises(ValueError):
        store.assign_task(t.slug, "gpt-99")


# ---------------------------------------------------------------- attempts


def test_increment_attempt(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    n1 = store.increment_attempt(t.slug)
    n2 = store.increment_attempt(t.slug)
    assert n1 == 1
    assert n2 == 2
    assert store.get_task(t.slug).attempt_count == 2  # type: ignore[union-attr]


# ---------------------------------------------------------------- comments + audit


def test_add_comment_writes_audit(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    store.add_comment(t.slug, actor="hive", comment="found relevant files")
    audit = store.audit_for(t.slug)
    actions = [a.action for a in audit]
    assert "create" in actions
    assert "comment" in actions


def test_full_audit_trail_covers_every_mutation(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    store.move_task(t.slug, schema.STATUS_READY)
    store.assign_task(t.slug, "hive")
    store.increment_attempt(t.slug)
    store.add_comment(t.slug, actor="hive", comment="working on it")
    actions = [a.action for a in store.audit_for(t.slug)]
    assert actions == [
        "create", "move", "assign", "attempt", "comment",
    ]


# ---------------------------------------------------------------- approvals


def test_request_and_approve(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    aid = store.request_approval(
        task_slug=t.slug, requested_by="hive",
        kind="external_service",
        summary="needs to hit GitHub API",
        payload={"url": "https://api.github.com/..."},
    )
    pending = store.list_pending_approvals()
    assert len(pending) == 1
    assert pending[0]["id"] == aid
    store.resolve_approval(aid, approved=True)
    assert store.list_pending_approvals() == []
    audit = [a.action for a in store.audit_for(t.slug)]
    assert "approval_request" in audit
    assert "approval_resolve" in audit


def test_deny_approval(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    aid = store.request_approval(
        task_slug=t.slug, requested_by="hive",
        kind="cost", summary="$0.20 OpenAI call",
    )
    store.resolve_approval(aid, approved=False)
    audit = store.audit_for(t.slug)
    resolve = [a for a in audit if a.action == "approval_resolve"][0]
    assert "denied" in resolve.detail


# ---------------------------------------------------------------- listing


def test_list_tasks_filters(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    store.upsert_project(Project(slug="ai", path="y", name="ai"))
    t1 = store.create_task(title="a", project_slug="fg")
    t2 = store.create_task(title="b", project_slug="ai")
    store.assign_task(t1.slug, "hive")
    fg_tasks = store.list_tasks(project_slug="fg")
    assert len(fg_tasks) == 1 and fg_tasks[0].slug == t1.slug
    hive_tasks = store.list_tasks(assignee="hive")
    assert len(hive_tasks) == 1 and hive_tasks[0].slug == t1.slug
    backlog = store.list_tasks(status=schema.STATUS_BACKLOG)
    assert len(backlog) == 2


def test_list_tasks_multi_status(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t1 = store.create_task(title="a", project_slug="fg")
    t2 = store.create_task(
        title="b", project_slug="fg", created_by="hive",
    )
    # t1=backlog, t2=proposed
    multi = store.list_tasks(
        status=[schema.STATUS_BACKLOG, schema.STATUS_PROPOSED],
    )
    assert len(multi) == 2


# ---------------------------------------------------------------- verify


def test_update_verify_results(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    t = store.update_verify_results(t.slug, {
        "tests": {"passed": 42, "failed": 0},
        "hive_verdict": "pass",
        "diff_path": "/tmp/diff",
    })
    assert t.verify_results["tests"]["passed"] == 42
    assert t.verify_results["hive_verdict"] == "pass"


def test_update_acceptance_criteria_tracks_progress(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(
        title="x", project_slug="fg",
        acceptance_criteria=[
            {"text": "tests pass", "checked": False},
            {"text": "doc updated", "checked": False},
        ],
    )
    t = store.update_acceptance_criteria(
        t.slug,
        [
            {"text": "tests pass", "checked": True},
            {"text": "doc updated", "checked": False},
        ],
    )
    audit = [a for a in store.audit_for(t.slug) if a.action == "update_criteria"]
    assert audit and "1/2 checked" in audit[0].detail


# ---------------------------------------------------------------- lessons (P5)


def test_add_and_recent_lessons(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    store.add_lesson("fg", "First lesson", task_slug="T-1", tags=["a"])
    store.add_lesson("fg", "Second lesson", task_slug="T-2")
    recent = store.recent_lessons("fg", limit=3)
    assert [l.body for l in recent] == ["Second lesson", "First lesson"]
    assert recent[0].task_slug == "T-2"
    assert recent[1].tags == ["a"]


def test_recent_lessons_scoped_by_project(store):
    store.add_lesson("fg", "fg lesson")
    store.add_lesson("other", "other lesson")
    assert [l.body for l in store.recent_lessons("fg")] == ["fg lesson"]
    assert store.count_lessons("fg") == 1
    assert store.count_lessons() == 2


def test_add_lesson_rejects_empty(store):
    with pytest.raises(ValueError):
        store.add_lesson("fg", "   ")


# ---------------------------------------------------------------- parallel (P6)


def test_project_parallel_roundtrip(store):
    store.upsert_project(
        Project(slug="par", path="x", name="par", parallel=True)
    )
    assert store.get_project("par").parallel is True
    store.set_project_parallel("par", parallel=False)
    assert store.get_project("par").parallel is False


def test_project_parallel_defaults_false(store):
    store.upsert_project(Project(slug="np", path="x", name="np"))
    assert store.get_project("np").parallel is False


def test_done_slugs(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    a = store.create_task(title="a", project_slug="fg")
    b = store.create_task(title="b", project_slug="fg")
    # Owner-created tasks start at BACKLOG; drive a -> done.
    for s in (schema.STATUS_READY, schema.STATUS_IN_PROGRESS,
              schema.STATUS_REVIEW, schema.STATUS_DONE):
        store.move_task(a.slug, s)
    slugs = store.done_slugs()
    assert a.slug in slugs and b.slug not in slugs


def test_record_turn_combines_heartbeat_and_tokens(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    store.record_turn(t.slug, hive_tokens=100)
    store.record_turn(t.slug, hive_tokens=50)
    got = store.get_task(t.slug)
    assert got.hive_tokens == 150          # accrued
    assert got.heartbeat_at                # stamped
    assert got.claude_tokens == 0          # untouched — never combined


def test_parse_fail_totals(store):
    store.upsert_project(Project(slug="fg", path="x", name="fg"))
    t = store.create_task(title="x", project_slug="fg")
    store.record_turn(t.slug, hive_tokens=10)   # turn 1
    store.record_turn(t.slug, hive_tokens=10)   # turn 2
    store.bump_parse_fail(t.slug)               # one bad reply
    fails, turns = store.parse_fail_totals()
    assert fails == 1 and turns == 2
    assert store.get_task(t.slug).agent_turns == 2
