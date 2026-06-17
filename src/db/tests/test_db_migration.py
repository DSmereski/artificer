"""Tests that 002_knowledge_schema.sql applies cleanly and is idempotent."""

import sqlite3
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "002_knowledge_schema.sql"
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in rows}


def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_applies_successfully():
    conn = _fresh_conn()
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    tables = _table_names(conn)
    assert "knowledge_files" in tables
    assert "tool_links" in tables
    conn.close()


def test_migration_is_idempotent():
    """Applying the migration twice does not raise."""
    sql = MIGRATION.read_text(encoding="utf-8")
    conn = _fresh_conn()
    conn.executescript(sql)
    conn.executescript(sql)  # second apply must be a no-op, not an error
    assert "knowledge_files" in _table_names(conn)
    conn.close()


def test_knowledge_files_has_expected_columns():
    conn = _fresh_conn()
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    cols = _columns(conn, "knowledge_files")
    for col in ("id", "title", "content_hash"):
        assert col in cols, f"knowledge_files missing column {col}"
    conn.close()


def test_tool_links_has_expected_columns():
    conn = _fresh_conn()
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    cols = _columns(conn, "tool_links")
    for col in ("tool_id", "knowledge_file_id", "link_type"):
        assert col in cols, f"tool_links missing column {col}"
    conn.close()


def test_tool_links_foreign_key_references_tools():
    """AC: FOREIGN KEY links tool_links.tool_id to tools.id."""
    conn = _fresh_conn()
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    fks = list(conn.execute("PRAGMA foreign_key_list(tool_links)"))
    # PRAGMA foreign_key_list columns: id, seq, table, from, to, ...
    match = [fk for fk in fks if fk[2] == "tools" and fk[3] == "tool_id" and fk[4] == "id"]
    assert match, f"no FK tool_links.tool_id -> tools.id; got {fks}"
    conn.close()


def test_foreign_key_is_enforced():
    """Inserting a tool_link with an unknown tool_id is rejected when FKs are on."""
    conn = _fresh_conn()
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO knowledge_files (id, title, content_hash) VALUES (?, ?, ?)",
        ("kf-1", "Notes", "abc123"),
    )
    raised = False
    try:
        conn.execute(
            "INSERT INTO tool_links (tool_id, knowledge_file_id, link_type) "
            "VALUES (?, ?, ?)",
            ("ghost-tool", "kf-1", "requires"),
        )
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "FK constraint did not reject orphan tool_id"
    conn.close()
