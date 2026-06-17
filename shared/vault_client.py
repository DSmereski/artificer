"""Client library for bots and Claude Code to read/write the vault.

Reads go direct to SQLite (read-only) and the vault filesystem.
Writes go over the daemon socket as JSON requests with an auth token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING


# Exponential backoff + jitter for SQLite "locked"/"busy" retries.
# Sequence (pre-jitter): ~0.05, 0.10, 0.20s; capped at 0.40s. The
# jitter window is [0.5x, 1.5x] of the base so concurrent retries
# don't dogpile and collide on the next attempt. Sync sleep is correct
# here: every caller is itself a sync method wrapping a sync SQLite
# read (the async vault-RPC paths don't take this branch).
_BUSY_BACKOFF_BASE_S = 0.05
_BUSY_BACKOFF_CAP_S = 0.40


def _busy_backoff_sleep(attempt: int) -> None:
    base = min(_BUSY_BACKOFF_BASE_S * (2 ** attempt), _BUSY_BACKOFF_CAP_S)
    time.sleep(base * (0.5 + random.random()))

from vault_writer.util import (
    audience_matches,
    coerce_audience,
    extract_wikilinks,
    parse_frontmatter,
)

if TYPE_CHECKING:
    from vault_writer.index import SearchResult

log = logging.getLogger("vault_client")


_VEC_DIM_RE = re.compile(r"FLOAT\[(\d+)\]")


def _default_auth_token() -> str | None:
    """Load the shared auth token from the conventional per-user path."""
    candidates = [
        Path(os.environ.get("VAULT_WRITER_TOKEN_PATH", "")),
        Path.home() / ".vault-writer" / "token",
    ]
    for p in candidates:
        if p and p.exists():
            try:
                return p.read_text(encoding="utf-8").strip() or None
            except OSError:
                continue
    return None


def _probe_vec_dimension(db_path: Path) -> int | None:
    """Read vec_notes DDL to determine the embedding dimension.

    Opens the DB read-only so the client cannot clobber schema.
    """
    if not db_path.exists():
        return None
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='vec_notes'"
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    m = _VEC_DIM_RE.search(row[0])
    return int(m.group(1)) if m else None


@dataclass(frozen=True, slots=True)
class VaultClient:
    vault_path: Path
    daemon_host: str
    daemon_port: int

    # ------------------------------------------------------------------ reads

    def preload_canon(self, agent: str) -> str:
        """Return all canon/*.md visible to `agent`, concatenated for system prompt use."""
        canon_dir = self.vault_path / "canon"
        if not canon_dir.is_dir():
            return ""

        parts: list[str] = []
        for path in sorted(canon_dir.glob("*.md")):
            raw = path.read_text(encoding="utf-8", errors="replace")
            frontmatter, body = parse_frontmatter(raw)
            audience = coerce_audience(frontmatter.get("audience"))
            if not audience_matches(agent, audience):
                continue
            parts.append(
                f"<!-- {path.relative_to(self.vault_path).as_posix()} -->\n{body.strip()}"
            )
        return "\n\n".join(parts)

    def preload_for_claude_code(self, cwd: Path, journal_tail: int = 20) -> str:
        """Return audience-filtered context for Claude Code session startup."""
        parts: list[str] = []

        canon = self.preload_canon("claude-code")
        if canon:
            parts.append(canon)

        jpath = self.vault_path / "journals" / "claude-code.md"
        if jpath.exists():
            raw = jpath.read_text(encoding="utf-8", errors="replace")
            _, body = parse_frontmatter(raw)
            segments = body.split("\n## ")
            if len(segments) > 1:
                tail = segments[-journal_tail:]
                head = tail[0].lstrip()
                rest = "\n## ".join(["", *tail[1:]]) if len(tail) > 1 else ""
                j_block = (head + rest).strip()
            else:
                j_block = body.strip()
            if j_block:
                parts.append(
                    f"<!-- journals/claude-code.md (tail {journal_tail}) -->\n{j_block}"
                )

        slug = cwd.name.lower().replace("_", "-")
        proj = self.vault_path / "projects" / f"{slug}.md"
        if proj.exists():
            raw = proj.read_text(encoding="utf-8", errors="replace")
            _, body = parse_frontmatter(raw)
            parts.append(f"<!-- projects/{slug}.md -->\n{body.strip()}")

        ops_dir = self.vault_path / "ops"
        if ops_dir.is_dir():
            for path in sorted(ops_dir.glob("*.md")):
                raw = path.read_text(encoding="utf-8", errors="replace")
                frontmatter, body = parse_frontmatter(raw)
                audience = coerce_audience(frontmatter.get("audience"))
                if not audience_matches("claude-code", audience):
                    continue
                parts.append(f"<!-- ops/{path.name} -->\n{body.strip()}")

        return "\n\n".join(parts)

    def search(
        self,
        query_embedding: list[float],
        *,
        k: int,
        audience: str,
        query_text: str | None = None,
    ) -> list["SearchResult"]:
        """Direct hybrid search against the daemon's DB (read-only).

        Forwards the original query text so the index can run its FTS5
        half alongside the vector kNN. Falls back to vector-only when
        `query_text` is None.

        Retries up to 3 times on `database is locked` so a search that
        races with the daemon's commit doesn't drop silently.
        """
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        dim = _probe_vec_dimension(db_path)
        if dim is None or len(query_embedding) != dim:
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.search(
                    query_embedding, k=k, audience=audience,
                    query_text=query_text,
                )
            except sqlite3.OperationalError as e:
                last_exc = e
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault search SQLite error: %s", e)
                return []
            finally:
                if idx is not None:
                    idx.close()
        log.warning("vault search gave up after retries: %s", last_exc)
        return []

    def neighbours(
        self,
        path: str,
        *,
        k: int,
        audience: str,
    ) -> list["SearchResult"]:
        """Top-k semantically similar notes to `path`. Excludes the
        seed note. Audience-filtered."""
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.neighbours(path, k=k, audience=audience)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault neighbours SQLite error: %s", e)
                return []
            finally:
                if idx is not None:
                    idx.close()
        return []

    # ------------------------------------------------------------- wikilinks

    def resolve_wikilinks(
        self,
        names: list[str],
        *,
        audience: str,
    ) -> list[tuple[str, str]]:
        """Resolve Obsidian [[Note Name]] refs to (relative_path, body) pairs.

        Matching is case-insensitive on filename without extension. Names may
        include a subfolder (e.g. "projects/ai-team"). Notes whose frontmatter
        audience excludes the caller are dropped.
        """
        out: list[tuple[str, str]] = []
        if not self.vault_path.is_dir():
            return out
        for raw_name in names:
            name = raw_name.strip()
            if not name:
                continue
            path = self._find_note_path(name)
            if path is None:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            frontmatter, body = parse_frontmatter(text)
            aud = coerce_audience(frontmatter.get("audience"))
            if not audience_matches(audience, aud):
                continue
            rel = path.relative_to(self.vault_path).as_posix()
            out.append((rel, body.strip()))
        return out

    def _find_note_path(self, name: str) -> Path | None:
        """Find a note by Obsidian-style name. Tries exact path,
        case-insensitive basename, then slugified basename (so a
        wikilink like `[[Vault Smoke Alpha]]` resolves to the actual
        file `vault-smoke-alpha.md` written by the slugifying daemon)."""
        # Obsidian treats backslash as part of the name; normalize.
        name = name.replace("\\", "/")
        # 1) Exact relative path.
        candidates = []
        if name.endswith(".md"):
            candidates.append(self.vault_path / name)
        else:
            candidates.append(self.vault_path / f"{name}.md")
        for c in candidates:
            if c.is_file() and self._within_vault(c):
                return c
        # 2) Case-insensitive basename search.
        base = Path(name).name.lower()
        if not base.endswith(".md"):
            base = base + ".md"
        for p in self.vault_path.rglob("*.md"):
            if p.name.lower() == base and self._within_vault(p):
                return p
        # 3) Slugified basename — the daemon slugifies titles into
        # filenames, so a wikilink using the human title needs a
        # corresponding slug match.
        try:
            from vault_writer.util import slugify
            slug_base = f"{slugify(Path(name).stem)}.md"
            if slug_base != base:
                for p in self.vault_path.rglob("*.md"):
                    if p.name.lower() == slug_base and self._within_vault(p):
                        return p
        except Exception:
            pass
        return None

    def _within_vault(self, p: Path) -> bool:
        """Defense-in-depth: ensure we never leave the vault via a crafted
        wikilink like [[../../etc/passwd]]."""
        try:
            p.resolve().relative_to(self.vault_path.resolve())
            return True
        except (ValueError, OSError):
            return False

    def expand_with_wikilinks(
        self,
        seeds: list[tuple[str, str]],
        *,
        audience: str,
        max_hops: int = 1,
        max_notes: int = 20,
    ) -> list[tuple[str, str]]:
        """Breadth-first walk: for each seed note, pull in [[linked]] notes.

        Returns a de-duplicated list of (relative_path, body) pairs including
        the seeds themselves. Walks at most `max_hops` levels deep and caps
        total output at `max_notes` to prevent context blowups on densely
        interlinked vaults.
        """
        seen: dict[str, str] = {path: body for path, body in seeds}
        frontier = list(seeds)
        for _hop in range(max(0, max_hops)):
            if not frontier or len(seen) >= max_notes:
                break
            next_frontier: list[tuple[str, str]] = []
            for _path, body in frontier:
                for link in extract_wikilinks(body):
                    for rel, lbody in self.resolve_wikilinks([link], audience=audience):
                        if rel in seen:
                            continue
                        seen[rel] = lbody
                        next_frontier.append((rel, lbody))
                        if len(seen) >= max_notes:
                            break
                    if len(seen) >= max_notes:
                        break
                if len(seen) >= max_notes:
                    break
            frontier = next_frontier
        return list(seen.items())

    # ----------------------------------------------------------------- socket

    async def learn(
        self,
        *,
        category: str,
        title: str,
        body: str,
        author: str,
        audience: list[str] | None = None,
        tags: list[str] | None = None,
        extra: dict | None = None,
        idempotency_key: str | None = None,
        timeout: float = 10.0,
        auth_token: str | None = None,
    ) -> dict | None:
        """Send a learn request to the daemon. Returns response dict, or None on failure."""
        token = auth_token if auth_token is not None else _default_auth_token()
        params: dict = {
            "category": category,
            "title": title,
            "body": body,
            "author": author,
            "audience": audience or ["all"],
            "tags": tags or [],
            "extra": extra or {},
        }
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        req: dict = {"method": "learn", "params": params}
        if token:
            req["auth"] = token

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.daemon_host, self.daemon_port),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError):
            log.warning("vault.learn: daemon unreachable")
            return None
        try:
            writer.write(json.dumps(req).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                return None
            return json.loads(line.decode())
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            log.warning("vault.learn: wire error: %s", e)
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def chat_log_append(
        self,
        *,
        bot: str,
        user_id: int,
        role: str,
        content: str,
        thread_id: str = "default",
        turn_id: str | None = None,
        parent_id: int | None = None,
        timeout: float = 5.0,
        auth_token: str | None = None,
    ) -> dict | None:
        """Send a chat_log_append request to the daemon. Idempotency
        is the caller's problem — the gateway turn finalizer guards
        against double-records via the `record_turn` shape it already
        uses for LLMClient.recent_messages."""
        token = auth_token if auth_token is not None else _default_auth_token()
        params: dict = {
            "bot": bot,
            "user_id": int(user_id),
            "role": role,
            "content": content,
            "thread_id": thread_id,
        }
        if turn_id:
            params["turn_id"] = turn_id
        if parent_id is not None:
            params["parent_id"] = int(parent_id)
        req: dict = {"method": "chat_log_append", "params": params}
        if token:
            req["auth"] = token
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.daemon_host, self.daemon_port),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError):
            log.warning("vault.chat_log_append: daemon unreachable")
            return None
        try:
            writer.write(json.dumps(req).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                return None
            return json.loads(line.decode())
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            log.warning("vault.chat_log_append: wire error: %s", e)
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def chat_log_clear(
        self,
        *,
        bot: str,
        user_id: int,
        timeout: float = 5.0,
        auth_token: str | None = None,
    ) -> dict | None:
        """Send a chat_log_clear request to the daemon, deleting all
        chat_log rows for (bot, user_id). Called from MemoryStore.reset
        so that SQLite chat history is wiped alongside the memory sidecar.

        Returns the parsed response dict on success, or None if the
        daemon is unreachable (best-effort; a missing daemon must not
        block the in-process sidecar reset).
        """
        return await self._send_rpc(
            "chat_log_clear",
            {"bot": bot, "user_id": int(user_id)},
            timeout=timeout,
            auth_token=auth_token,
            log_label="vault.chat_log_clear",
        )

    async def _send_rpc(
        self, method: str, params: dict,
        *, timeout: float, auth_token: str | None,
        log_label: str,
    ) -> dict | None:
        """Single-shot daemon RPC. Returns the parsed response dict
        or None on any wire/timeout/JSON error. Auth token defaults
        to the gateway's configured value."""
        token = auth_token if auth_token is not None else _default_auth_token()
        req: dict = {"method": method, "params": params}
        if token:
            req["auth"] = token
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.daemon_host, self.daemon_port),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError):
            log.warning("vault.%s: daemon unreachable", log_label)
            return None
        try:
            writer.write(json.dumps(req).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                return None
            return json.loads(line.decode())
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            log.warning("vault.%s: wire error: %s", log_label, e)
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def thread_create(
        self, *, thread_id: str, bot: str, user_id: int,
        title: str | None = None,
        parent_thread_id: str | None = None,
        fork_point_turn_id: str | None = None,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        params: dict = {
            "thread_id": thread_id, "bot": bot, "user_id": int(user_id),
        }
        if title is not None:
            params["title"] = title
        if parent_thread_id:
            params["parent_thread_id"] = parent_thread_id
        if fork_point_turn_id:
            params["fork_point_turn_id"] = fork_point_turn_id
        return await self._send_rpc(
            "thread_create", params,
            timeout=timeout, auth_token=auth_token,
            log_label="thread_create",
        )

    async def thread_archive(
        self, *, thread_id: str,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "thread_archive", {"thread_id": thread_id},
            timeout=timeout, auth_token=auth_token,
            log_label="thread_archive",
        )

    async def thread_unarchive(
        self, *, thread_id: str,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "thread_unarchive", {"thread_id": thread_id},
            timeout=timeout, auth_token=auth_token,
            log_label="thread_unarchive",
        )

    async def thread_rename(
        self, *, thread_id: str, title: str,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "thread_rename",
            {"thread_id": thread_id, "title": title},
            timeout=timeout, auth_token=auth_token,
            log_label="thread_rename",
        )

    async def thread_pin(
        self, *, thread_id: str, pinned: bool,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "thread_pin",
            {"thread_id": thread_id, "pinned": bool(pinned)},
            timeout=timeout, auth_token=auth_token,
            log_label="thread_pin",
        )

    async def thread_set_title(
        self, *, thread_id: str, title: str,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "thread_set_title",
            {"thread_id": thread_id, "title": title},
            timeout=timeout, auth_token=auth_token,
            log_label="thread_set_title",
        )

    async def thread_touch(
        self, *, thread_id: str,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "thread_touch", {"thread_id": thread_id},
            timeout=timeout, auth_token=auth_token,
            log_label="thread_touch",
        )

    async def thread_fork(
        self, *, new_thread_id: str, source_thread_id: str,
        bot: str, user_id: int, title: str | None = None,
        fork_point_turn_id: str | None = None,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        params: dict = {
            "new_thread_id": new_thread_id,
            "source_thread_id": source_thread_id,
            "bot": bot, "user_id": int(user_id),
        }
        if title is not None:
            params["title"] = title
        if fork_point_turn_id:
            params["fork_point_turn_id"] = fork_point_turn_id
        return await self._send_rpc(
            "thread_fork", params,
            timeout=timeout, auth_token=auth_token,
            log_label="thread_fork",
        )

    async def chat_pin(
        self, *, turn_id: str, bot: str, user_id: int,
        pinned: bool = True,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        return await self._send_rpc(
            "chat_pin",
            {
                "turn_id": turn_id, "bot": bot, "user_id": int(user_id),
                "pinned": bool(pinned),
            },
            timeout=timeout, auth_token=auth_token,
            log_label="chat_pin",
        )

    async def entity_page_update(
        self, *, slug: str, kind: str, title: str,
        compiled_truth: str = "",
        timeline_entry: str = "",
        relationships: list[dict] | None = None,
        timeout: float = 5.0, auth_token: str | None = None,
    ) -> dict | None:
        """Upsert an entity page. Empty `compiled_truth` preserves
        existing content; `timeline_entry` always appends when given.
        `relationships` carries graphify-shaped edges
        ({target_slug, label, confidence}) — None means "leave the
        existing edges in place," explicit [] clears them. Returns
        daemon response containing prior_compiled_truth +
        prior_existed."""
        params: dict = {
            "slug": slug, "kind": kind, "title": title,
            "compiled_truth": compiled_truth,
            "timeline_entry": timeline_entry,
        }
        if relationships is not None:
            params["relationships"] = list(relationships)
        return await self._send_rpc(
            "entity_page_update",
            params,
            timeout=timeout, auth_token=auth_token,
            log_label="entity_page_update",
        )

    # ---------------------------------------------------------------- reads

    def list_threads(
        self, *, bot: str, user_id: int,
        include_archived: bool = False, limit: int = 100,
    ) -> list[dict]:
        """Direct read of chat_thread, newest-first by last_active_at.
        Empty list if the DB or vec_notes aren't initialized yet."""
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        idx = None
        try:
            idx = VaultIndex.open(db_path, dimension=dim)
            return idx.thread_list(
                bot=bot, user_id=int(user_id),
                include_archived=include_archived, limit=int(limit),
            )
        except sqlite3.OperationalError as e:
            log.warning("vault list_threads error: %s", e)
            return []
        finally:
            if idx is not None:
                idx.close()

    def search_threads(
        self, *, bot: str, user_id: int, query: str, limit: int = 20,
    ) -> list[dict]:
        """FTS + title-LIKE search over chat_thread. Direct sqlite read,
        no daemon RPC (read-only, mirrors list_threads pattern).
        Returns [] on missing DB, import error, or OperationalError."""
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        idx = None
        try:
            idx = VaultIndex.open(db_path, dimension=dim)
            return idx.thread_search(
                bot=bot, user_id=int(user_id),
                query=query, limit=int(limit),
            )
        except sqlite3.OperationalError as e:
            log.warning("vault search_threads error: %s", e)
            return []
        finally:
            if idx is not None:
                idx.close()

    def get_thread(self, thread_id: str) -> dict | None:
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return None
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return None
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return None
        idx = None
        try:
            idx = VaultIndex.open(db_path, dimension=dim)
            return idx.thread_get(thread_id)
        except sqlite3.OperationalError:
            return None
        finally:
            if idx is not None:
                idx.close()

    def get_chat_turn(self, turn_id: str) -> list[dict]:
        """Returns the (user, assistant) rows for a turn_id, in
        insertion order. Used by the pin-to-vault endpoint."""
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        idx = None
        try:
            idx = VaultIndex.open(db_path, dimension=dim)
            return idx.chat_get_by_turn_id(turn_id)
        except sqlite3.OperationalError:
            return []
        finally:
            if idx is not None:
                idx.close()

    def recent_chat(
        self,
        *,
        bot: str,
        user_id: int,
        limit: int = 50,
        thread_id: str | None = None,
    ) -> list[dict]:
        """Return the most recent `limit` chat_log rows for (bot, user_id),
        oldest-first.  This is the persistent counterpart to
        LLMClient.recent_messages — it survives gateway restarts and
        retains turns that have aged out of the in-memory rolling buffer.

        Returns a list of ``{"role": ..., "content": ...}`` dicts in
        chronological order, exactly the same shape as
        LLMClient.recent_messages so callers can transparently merge them.
        """
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.chat_log_recent(
                    bot=bot, user_id=user_id, limit=limit,
                    thread_id=thread_id,
                )
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault recent_chat error: %s", e)
                return []
            finally:
                if idx is not None:
                    idx.close()
        return []

    def search_chat(
        self,
        *,
        bot: str,
        user_id: int,
        query_text: str,
        limit: int = 20,
        thread_id: str | None = None,
    ) -> list[dict]:
        """Direct FTS5 read against chat_log. No vector half — chat
        text is short and the FTS index handles "what did we say
        about X" without the embed cost. Read-only.
        """
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        # We need *some* dimension for VaultIndex.open(); probe the
        # existing schema rather than picking a magic number that
        # would mismatch on the next note write.
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.search_chat(
                    bot=bot, user_id=user_id,
                    query_text=query_text, limit=limit,
                    thread_id=thread_id,
                )
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault search_chat error: %s", e)
                return []
            finally:
                if idx is not None:
                    idx.close()
        return []

    def count_chat_pinned_since(
        self, *, bot: str, user_id: int, since_epoch: int,
    ) -> int:
        """Returns the count of pinned chat_log rows for (bot,user_id)
        created at or after `since_epoch`. Read-only, direct sqlite.
        """
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return 0
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return 0
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return 0
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.count_chat_pinned_since(
                    bot=bot, user_id=user_id, since_epoch=since_epoch,
                )
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault count_chat_pinned_since error: %s", e)
                return 0
            finally:
                if idx is not None:
                    idx.close()
        return 0

    def search_notes_fts(
        self, *, query_text: str, audience: str = "all", limit: int = 20,
    ) -> list[dict]:
        """FTS5-only notes search — no embedding round-trip. Used by
        the `/v1/search` fan-out where we already have many other
        surfaces to query and don't want to pay for the vector half.
        """
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.search_notes_fts(
                    query_text, audience=audience, limit=limit,
                )
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault search_notes_fts error: %s", e)
                return []
            finally:
                if idx is not None:
                    idx.close()
        return []

    def search_entity_pages(
        self, *, query_text: str, limit: int = 20,
    ) -> list[dict]:
        """Direct FTS5 read against entity_page. No audience field on
        entity_page rows — they're inherently single-owner."""
        db_path = self.vault_path / ".vault-writer" / "vault.db"
        if not db_path.exists():
            return []
        try:
            from vault_writer.index import VaultIndex
        except ImportError:
            return []
        dim = _probe_vec_dimension(db_path)
        if dim is None:
            return []
        for attempt in range(3):
            idx = None
            try:
                idx = VaultIndex.open(db_path, dimension=dim)
                return idx.entity_page_search(query_text, limit=limit)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    _busy_backoff_sleep(attempt)
                    continue
                log.warning("vault search_entity_pages error: %s", e)
                return []
            finally:
                if idx is not None:
                    idx.close()
        return []

    async def ping(self, timeout: float = 2.0) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.daemon_host, self.daemon_port),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError):
            return False
        try:
            writer.write(json.dumps({"method": "ping", "params": {}}).encode() + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                return False
            resp = json.loads(line.decode())
            return bool(resp.get("pong"))
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return False
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
