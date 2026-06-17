"""QA acceptance tests for T-0358: knowledge schema migration.

Each test maps to an explicit acceptance criterion:
  AC1 - 002_knowledge_schema.sql applies successfully under pytest.
  AC2 - file exists with CREATE TABLE for knowledge_files and tool_links.
  AC3 - file includes a FOREIGN KEY linking tool_links.tool_id -> tools.id.

Idempotency (an explicit task requirement) is also covered.
"""

import re
import sqlite3
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "002_knowledge_schema.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _apply(conn: sqlite3.Connection) -> None:
    conn.executescript(_sql())


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in rows}


# --- AC2: file exists --------------------------------------------------------


def test_ac2_migration_file_exists():
    assert MIGRATION.exists(), f"expected migration at {MIGRATION}"
    assert MIGRATION.name == "002_knowledge_schema.sql"


# --- AC2: CREATE TABLE for both tables (source-text level) -------------------


@pytest.mark.parametrize("table", ["knowledge_files", "tool_links"])
def test_ac2_source_has_create_table(table):
    """The file literally contains a CREATE TABLE for each required table."""
    pattern = re.compile(
        rf"create\s+table\s+(if\s+not\s+exists\s+)?{table}\b", re.IGNORECASE
    )
    assert pattern.search(_sql()), f"no CREATE TABLE for {table} in migration"


# --- AC1: applies successfully + creates the tables -------------------------


def test_ac1_migration_applies_and_creates_tables():
    conn = _conn()
    _apply(conn)  # must not raise
    tables = _tables(conn)
    assert {"knowledge_files", "tool_links"} <= tables
    conn.close()


# --- Idempotency (task body requirement) ------------------------------------


def test_idempotent_double_apply():
    conn = _conn()
    _apply(conn)
    _apply(conn)  # second run must be a no-op, not an error
    assert {"knowledge_files", "tool_links"} <= _tables(conn)
    conn.close()


# --- AC3: FOREIGN KEY tool_links.tool_id -> tools.id ------------------------


def test_ac3_source_has_foreign_key_clause():
    """Source text declares the FK from tool_id to tools(id)."""
    sql = _sql().lower()
    assert "foreign key" in sql, "no FOREIGN KEY clause present"
    pattern = re.compile(
        r"foreign\s+key\s*\(\s*tool_id\s*\)\s*references\s+tools\s*\(\s*id\s*\)",
        re.IGNORECASE,
    )
    assert pattern.search(_sql()), "missing FK tool_links.tool_id -> tools(id)"


def test_ac3_runtime_foreign_key_registered():
    """SQLite reports the FK after the schema is applied."""
    conn = _conn()
    _apply(conn)
    fks = list(conn.execute("PRAGMA foreign_key_list(tool_links)"))
    # PRAGMA columns: id, seq, table, from, to, on_update, on_delete, match
    match = [fk for fk in fks if fk[2] == "tools" and fk[3] == "tool_id" and fk[4] == "id"]
    assert match, f"FK tool_links.tool_id -> tools.id not found; got {fks}"
    conn.close()


def test_ac3_foreign_key_rejects_orphan_tool_id():
    """With FKs enforced, an unknown tool_id is rejected on insert."""
    conn = _conn()
    _apply(conn)
    conn.execute(
        "INSERT INTO knowledge_files (id, title, content_hash) VALUES (?, ?, ?)",
        ("kf-qa", "QA", "hash-qa"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tool_links (tool_id, knowledge_file_id, link_type) "
            "VALUES (?, ?, ?)",
            ("missing-tool", "kf-qa", "requires"),
        )
    conn.close()


def test_ac3_foreign_key_accepts_valid_tool_id():
    """A tool_link with an existing tool_id and knowledge_file_id is accepted."""
    conn = _conn()
    _apply(conn)
    conn.execute("INSERT INTO tools (id, name) VALUES (?, ?)", ("tool-1", "Grep"))
    conn.execute(
        "INSERT INTO knowledge_files (id, title, content_hash) VALUES (?, ?, ?)",
        ("kf-ok", "Notes", "hash-ok"),
    )
    conn.execute(
        "INSERT INTO tool_links (tool_id, knowledge_file_id, link_type) "
        "VALUES (?, ?, ?)",
        ("tool-1", "kf-ok", "requires"),
    )
    count = conn.execute("SELECT COUNT(*) FROM tool_links").fetchone()[0]
    assert count == 1
    conn.close()
