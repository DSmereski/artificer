"""Async daemon: TCP server + watchdog + git push loop + learn handler."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

import yaml
from watchdog.observers import Observer

from vault_writer import __version__
from vault_writer.config import Config
from vault_writer.index import VaultIndex
from vault_writer.protocol import (
    AuthRequired,
    ChatLogAppendRequest,
    ChatLogAppendResponse,
    ChatLogClearRequest,
    ChatLogClearResponse,
    ChatPinRequest,
    ChatPinResponse,
    EntityPageUpdateRequest,
    EntityPageUpdateResponse,
    ErrorResponse,
    LearnRequest,
    LearnResponse,
    PingRequest,
    PingResponse,
    ThreadArchiveRequest,
    ThreadArchiveResponse,
    ThreadCreateRequest,
    ThreadCreateResponse,
    ThreadForkRequest,
    ThreadForkResponse,
    ThreadPinRequest,
    ThreadPinResponse,
    ThreadRenameRequest,
    ThreadRenameResponse,
    ThreadSetTitleRequest,
    ThreadSetTitleResponse,
    ThreadTouchRequest,
    ThreadTouchResponse,
    ThreadUnarchiveRequest,
    ThreadUnarchiveResponse,
    decode_request,
    encode_response,
)
from vault_writer.util import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    audience_matches,
    confine_path,
    parse_frontmatter,
    scrub_secrets,
    slugify,
)
from vault_writer.watcher import (
    NoteContent,
    NoteTooLarge,
    VaultEventHandler,
    parse_note,
)

log = logging.getLogger("vault_writer.daemon")


from shared.categories import CATEGORY_FOLDER as _CATEGORY_FOLDER

# Categories where the file gets an appended dated entry per write rather than
# an overwrite.
_APPEND_CATEGORIES = frozenset({"journal", "session", "person"})

# Env vars we strip before spawning git to avoid hostile ones (proxy, ssh,
# config dirs, template dirs) redirecting the push.
_GIT_ENV_DROP = frozenset({
    "GIT_SSH_COMMAND", "GIT_SSH", "GIT_PROXY_COMMAND",
    "GIT_EXTERNAL_DIFF", "GIT_TEMPLATE_DIR",
    "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
})

# How long to remember our own writes so watchdog doesn't re-embed them.
_SELF_EVENT_TTL_SECONDS = 5.0

# After this many consecutive embedding failures for a given path, the daemon
# marks the path as permanently skipped for the lifetime of the process.
# Restarts clear the counter so a config fix (e.g. larger Ollama context
# window) is picked up automatically on the next start.
_EMBED_FAIL_LIMIT = 3


class EmbedderLike(Protocol):
    dimension: int

    async def embed(
        self,
        text: str,
        *,
        kind: str = "document",
    ) -> list[float]: ...

    async def embed_chunks(
        self,
        text: str,
        *,
        kind: str = "document",
        chunk_size: int | None = None,
    ) -> list[list[float]]: ...


class Daemon:
    """Long-lived asyncio server that keeps the vault index in sync."""

    def __init__(self, config: Config, embedder: EmbedderLike) -> None:
        if embedder.dimension != config.embedding_dimension:
            raise ValueError(
                f"embedder dim {embedder.dimension} != config {config.embedding_dimension}"
            )
        self._config = config
        self._embedder = embedder
        self._db_path = config.vault_path / ".vault-writer" / "vault.db"
        self._queue: asyncio.Queue[tuple[str, Path]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._commit_task: asyncio.Task | None = None
        self._server: asyncio.Server | None = None
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dirty: bool = False
        # (absolute-path, mtime_ns) entries for writes the daemon just made,
        # so the watchdog callback ignores its own echo.
        self._self_writes: dict[str, float] = {}
        # Consecutive embedding-failure counters keyed by relative path.
        # Once a path reaches _EMBED_FAIL_LIMIT failures it is skipped for the
        # lifetime of this process — preventing infinite 500-spam for files
        # like imagegen-loras.md that permanently exceed the model's context.
        self._embed_failures: dict[str, int] = {}
        self.index: VaultIndex | None = None
        self.bound_port: int = 0

    # ============================================================ lifecycle

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.index = VaultIndex.open(self._db_path, self._config.embedding_dimension)
        # Existing vaults whose index predates the FTS5 column have an
        # empty notes_fts; backfill once on startup so hybrid search
        # works immediately without forcing a re-walk of the FS.
        try:
            n = self.index.backfill_fts_if_empty()
            if n > 0:
                log.info("vault index: backfilled FTS for %d existing notes", n)
        except Exception as e:  # noqa: BLE001
            log.warning("FTS backfill failed (continuing): %s", e)

        self._worker_task = asyncio.create_task(self._worker(), name="vault-worker")
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._config.daemon_bind_host,
            port=self._config.daemon_bind_port,
            limit=256 * 1024,
        )
        sockets = self._server.sockets or []
        self.bound_port = sockets[0].getsockname()[1] if sockets else 0
        log.info(
            "vault-writer listening on %s:%d (auth=%s)",
            self._config.daemon_bind_host, self.bound_port,
            "on" if self._config.auth.token() else "off",
        )

        self._observer = Observer()
        handler = VaultEventHandler(
            vault_root=self._config.vault_path,
            on_change=lambda p: self._schedule(("upsert", p)),
            on_delete=lambda p: self._schedule(("delete", p)),
        )
        self._observer.schedule(handler, str(self._config.vault_path), recursive=True)
        self._observer.start()

        if self._config.scan.initial_full_scan:
            on_disk_paths = list(self._config.vault_path.rglob("*.md"))

            # Orphan reconciliation runs synchronously before any upserts
            # are queued so note IDs freed by DELETE cannot be reused by a
            # concurrently-processing worker and falsely appear as leftover
            # chunk rows.  Safety guard: if zero files were found on disk we
            # assume the vault_path is misconfigured or a mount is not ready
            # — we do NOT purge the entire index in that case.
            if self._config.scan.reconcile_orphans and len(on_disk_paths) > 0:
                self._reconcile_orphans_sync(on_disk_paths)

            for p in on_disk_paths:
                await self._queue.put(("upsert", p))

        if self._config.gitea.push_on_write:
            self._commit_task = asyncio.create_task(
                self._commit_loop(), name="vault-commit"
            )

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        if self._commit_task:
            self._commit_task.cancel()
            try:
                await self._commit_task
            except asyncio.CancelledError:
                pass
            await self._flush_commit()  # final flush before exit
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self.index is not None:
            self.index.close()

    async def wait_idle(self, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._queue.empty():
                await asyncio.sleep(0.5)
                if self._queue.empty():
                    return
            await asyncio.sleep(0.1)
        raise TimeoutError("daemon did not become idle")

    # ============================================================= worker

    def _schedule(self, item: tuple[str, Path]) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _mark_self_write(self, path: Path) -> None:
        """Record our own write so the next watchdog echo is ignored."""
        self._prune_self_writes()
        self._self_writes[str(path.resolve())] = time.monotonic()

    def _is_self_echo(self, path: Path) -> bool:
        self._prune_self_writes()
        key = str(path.resolve())
        if key in self._self_writes:
            del self._self_writes[key]   # consume once; future edits are real
            return True
        return False

    def _prune_self_writes(self) -> None:
        now = time.monotonic()
        stale = [k for k, t in self._self_writes.items()
                 if now - t > _SELF_EVENT_TTL_SECONDS]
        for k in stale:
            del self._self_writes[k]

    def _reconcile_orphans_sync(self, on_disk_paths: list[Path]) -> None:
        """Delete index rows whose backing .md file no longer exists on disk.

        Called synchronously during ``initial_full_scan``, before any upsert
        events are queued, so IDs freed by DELETE cannot be reused by the
        worker and falsely appear as dangling chunk rows.

        Uses the existing ``index.delete`` path so all related rows
        (notes, notes_fts, vec_notes, note_chunks, vec_note_chunks) are
        removed consistently — no hand-rolled multi-table DELETEs.

        Safety guard: only runs when the on-disk set is non-empty.  If zero
        files were found (vault_path misconfigured, mount not ready) we skip
        so a transient empty-directory cannot wipe the entire index.
        """
        if self.index is None:
            return
        vault_resolved = self._config.vault_path.resolve()
        # Build the set of relative POSIX paths that exist on disk.
        on_disk_rel: set[str] = set()
        for p in on_disk_paths:
            try:
                on_disk_rel.add(p.resolve().relative_to(vault_resolved).as_posix())
            except ValueError:
                pass  # outside vault root — skip

        # Fetch all indexed paths and collect those absent from disk.
        indexed = self.index._conn.execute(
            "SELECT path FROM notes"
        ).fetchall()
        orphan_paths = [
            row["path"] for row in indexed
            if row["path"] not in on_disk_rel
        ]
        if not orphan_paths:
            return
        for rel_path in orphan_paths:
            self.index.delete(rel_path)
        log.info(
            "vault index: reconciled %d orphan note(s) removed from index "
            "(files deleted outside watchdog)",
            len(orphan_paths),
        )

    async def _worker(self) -> None:
        while True:
            op, path = await self._queue.get()
            try:
                if op == "upsert":
                    await self._do_upsert(path)
                elif op == "delete":
                    await self._do_delete(path)
            except Exception:  # noqa: BLE001  (boundary: keep worker alive)
                log.exception("worker failed on %s %s", op, path)
            finally:
                self._queue.task_done()

    async def _do_upsert(self, path: Path) -> None:
        if not path.exists():
            return
        if self._is_self_echo(path):
            return
        try:
            note: NoteContent = parse_note(path, self._config.vault_path)
        except NoteTooLarge as e:
            log.warning("skipping oversized note: %s", e)
            return
        except ValueError:
            return  # outside vault

        if any(part.startswith(".") for part in Path(note.rel_path).parts):
            return  # .vault-writer, .obsidian, etc.

        # Canon integrity: only human-authored notes may live under canon/.
        if note.rel_path.startswith("canon/") and note.author != "human":
            log.warning(
                "rejecting non-human canon write %s (author=%s) — file remains "
                "on disk but will not be indexed",
                note.rel_path, note.author,
            )
            return

        # Circuit breaker: skip paths that have repeatedly failed embedding so
        # we don't spam Ollama (and the log) on every watchdog event.
        if self._embed_failures.get(note.rel_path, 0) >= _EMBED_FAIL_LIMIT:
            log.debug(
                "embed skip (already failed %d times): %s",
                _EMBED_FAIL_LIMIT, note.rel_path,
            )
            return

        from vault_writer.embedder import EmbeddingError
        # Embed ALL chunks with the "document" task prefix so nomic's
        # asymmetric alignment is active. The first-chunk vector goes into
        # the legacy vec_notes table (unchanged API); all chunk vectors go
        # into the new note_chunks / vec_note_chunks tables so queries that
        # only match text in chunk 2+ can still recall the note.
        try:
            all_vecs = await self._embedder.embed_chunks(
                note.body, kind="document",
                chunk_size=self._config.chunk_max_chars,
            )
        except EmbeddingError as exc:
            failures = self._embed_failures.get(note.rel_path, 0) + 1
            self._embed_failures[note.rel_path] = failures
            if failures >= _EMBED_FAIL_LIMIT:
                log.error(
                    "embed permanently skipped after %d failures "
                    "(embed_status=skipped path=%s): %s",
                    failures, note.rel_path, exc,
                )
            else:
                log.warning(
                    "embed failed (attempt %d/%d) for %s: %s",
                    failures, _EMBED_FAIL_LIMIT, note.rel_path, exc,
                )
            return

        # Success — clear any prior failure count so a fixed file re-indexes.
        self._embed_failures.pop(note.rel_path, None)

        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        # Primary (first-chunk) vector stored in legacy vec_notes.
        self.index.upsert(
            path=note.rel_path,
            note_type=note.note_type,
            author=note.author,
            audience=list(note.audience),
            frontmatter=note.frontmatter,
            body=note.body,
            embedding=all_vecs[0],
        )
        # All-chunk vectors stored in note_chunks / vec_note_chunks.
        note_row = self.index._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (note.rel_path,)
        ).fetchone()
        if note_row is not None:
            self.index.upsert_chunks(int(note_row["id"]), all_vecs)

    async def _do_delete(self, path: Path) -> None:
        # Windows watchdog can emit a spurious "deleted" event during an
        # atomic replace (os.replace of existing file). If the file is back
        # on disk by the time we process the event, it wasn't really deleted.
        if path.exists():
            return
        try:
            rel = path.resolve().relative_to(
                self._config.vault_path.resolve()
            ).as_posix()
        except ValueError:
            return
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.delete(rel)

    # =========================================================== learn RPC

    def _learn_target(self, req: LearnRequest) -> Path:
        folder = _CATEGORY_FOLDER.get(req.category)
        if folder is None:
            raise ValueError(f"unknown category: {req.category}")

        vault = self._config.vault_path

        if req.category == "journal":
            target = vault / folder / f"{slugify(req.author)}.md"
        elif req.category == "session":
            today = dt.date.today()
            target = (
                vault / folder / f"{today.year:04d}" / f"{today.month:02d}"
                / f"{today.isoformat()}-{slugify(req.title)}.md"
            )
        elif req.category == "knowledge":
            today = dt.date.today()
            target = (
                vault / folder / f"{today.year:04d}" / f"{today.month:02d}"
                / f"{slugify(req.title)}.md"
            )
        elif req.category == "person":
            discord_id = req.extra.get("discord_id") if isinstance(req.extra, dict) else None
            target = vault / folder / f"{slugify(discord_id or req.title)}.md"
        else:
            # system | project | tool | ops
            target = vault / folder / f"{slugify(req.title)}.md"

        # Belt + suspenders: reject if anything resolved outside the vault.
        confine_path(target, vault)
        return target

    def _frontmatter_for_learn(
        self, req: LearnRequest, existing: dict | None = None
    ) -> dict:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        type_for = {
            "knowledge": "knowledge", "system": "system", "project": "project",
            "tool": "tool", "ops": "ops", "person": "person",
            "journal": "journal", "session": "session",
        }
        base = dict(existing or {})
        new_tags = list(dict.fromkeys([*(base.get("tags") or []), *req.tags])) \
            if req.tags or base.get("tags") else None
        fm = {
            **base,
            "type": base.get("type", type_for[req.category]),
            # Last-write-wins on title so a re-learn from the user
            # (or a rename via the app) actually updates the
            # frontmatter title. `req.title` is required (min_length=1)
            # so this is never None; the `or` is a belt-and-suspenders
            # for unexpected stripped-empty inputs.
            "title": (req.title or "").strip() or base.get("title"),
            "author": base.get("author", req.author),
            "audience": req.audience,
            "created": base.get("created", now),
            "updated": now,
        }
        if new_tags:
            fm["tags"] = new_tags
        # extra fields can add new frontmatter but never overwrite existing keys.
        for k, v in (req.extra or {}).items():
            fm.setdefault(k, v)
        return fm

    @staticmethod
    def _render_file(frontmatter: dict, body: str) -> str:
        fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{body.rstrip()}\n"

    def _atomic_write(self, target: Path, content: str) -> None:
        """Write `content` to `target` via temp-file + os.replace to avoid
        partial reads by Obsidian/Voicetree/watchdog mid-write."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        self._mark_self_write(target)

    async def _do_learn(self, req: LearnRequest) -> LearnResponse:
        if req.category == "canon":
            raise ValueError("canon is human-only; cannot write via learn")

        target = self._learn_target(req)

        existing_fm: dict | None = None
        existing_body = ""
        created = not target.exists()
        if not created:
            try:
                size = target.stat().st_size
            except OSError:
                size = 0
            if size > 0:
                raw = target.read_text(encoding="utf-8", errors="replace")
                try:
                    existing_fm, existing_body = parse_frontmatter(raw)
                except Exception:  # noqa: BLE001
                    log.warning("corrupt frontmatter in %s, treating as empty", target)
                    existing_fm, existing_body = {}, raw

        if req.category in _APPEND_CATEGORIES:
            now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n## {now} — {req.title}\n\n{req.body.strip()}\n"
            new_body = (
                existing_body.rstrip() + "\n" + entry
                if existing_body else entry.lstrip("\n")
            )
        else:
            new_body = req.body.strip() + "\n"

        fm = self._frontmatter_for_learn(req, existing=existing_fm)
        self._atomic_write(target, self._render_file(fm, new_body))

        rel = target.relative_to(self._config.vault_path).as_posix()
        # Embed all chunks with the "document" task prefix. Primary vector
        # (chunk 0) feeds the legacy vec_notes table; all chunks feed
        # note_chunks / vec_note_chunks for full-body recall.
        all_vecs = await self._embedder.embed_chunks(
            new_body, kind="document",
            chunk_size=self._config.chunk_max_chars,
        )
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.upsert(
            path=rel,
            note_type=fm.get("type", req.category),
            author=req.author,
            audience=req.audience,
            frontmatter=fm,
            body=new_body,
            embedding=all_vecs[0],
        )
        note_row = self.index._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (rel,)
        ).fetchone()
        if note_row is not None:
            self.index.upsert_chunks(int(note_row["id"]), all_vecs)

        self._dirty = True
        return LearnResponse(ok=True, path=rel, created=created)

    # ============================================================== chat_log

    def _do_chat_log_append(
        self, req: ChatLogAppendRequest,
    ) -> ChatLogAppendResponse:
        """Append a Hive turn to the chat_log table. Synchronous —
        SQLite insert is fast and the request handler awaits it."""
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        rowid = self.index.chat_log_append(
            thread_id=req.thread_id,
            bot=req.bot,
            user_id=int(req.user_id),
            role=req.role,
            content=req.content,
            turn_id=req.turn_id,
            parent_id=req.parent_id,
            created_at=int(time.time()),
        )
        return ChatLogAppendResponse(ok=True, id=rowid)

    def _do_chat_log_clear(
        self, req: ChatLogClearRequest,
    ) -> ChatLogClearResponse:
        """Delete all chat_log rows for (bot, user_id). Called during
        MemoryStore.reset to prevent info-disclosure across resets."""
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        deleted = self.index.chat_log_clear(bot=req.bot, user_id=int(req.user_id))
        return ChatLogClearResponse(ok=True, deleted=deleted)

    # =============================================================== threads

    def _do_thread_create(
        self, req: ThreadCreateRequest,
    ) -> ThreadCreateResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        rows = self.index.thread_create(
            thread_id=req.thread_id, bot=req.bot,
            user_id=int(req.user_id), title=req.title,
            created_at=int(time.time()),
            parent_thread_id=req.parent_thread_id,
            fork_point_turn_id=req.fork_point_turn_id,
        )
        return ThreadCreateResponse(
            ok=True, thread_id=req.thread_id, created=bool(rows),
        )

    def _do_thread_archive(
        self, req: ThreadArchiveRequest,
    ) -> ThreadArchiveResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.thread_archive(
            thread_id=req.thread_id, archived_at=int(time.time()),
        )
        return ThreadArchiveResponse(ok=True)

    def _do_thread_unarchive(
        self, req: ThreadUnarchiveRequest,
    ) -> ThreadUnarchiveResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.thread_unarchive(thread_id=req.thread_id)
        return ThreadUnarchiveResponse(ok=True)

    def _do_thread_rename(
        self, req: ThreadRenameRequest,
    ) -> ThreadRenameResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.thread_rename(thread_id=req.thread_id, title=req.title)
        return ThreadRenameResponse(ok=True)

    def _do_thread_pin(
        self, req: ThreadPinRequest,
    ) -> ThreadPinResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.thread_pin(thread_id=req.thread_id, pinned=req.pinned)
        return ThreadPinResponse(ok=True)

    def _do_thread_set_title(
        self, req: ThreadSetTitleRequest,
    ) -> ThreadSetTitleResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.thread_set_title(thread_id=req.thread_id, title=req.title)
        return ThreadSetTitleResponse(ok=True)

    def _do_thread_touch(
        self, req: ThreadTouchRequest,
    ) -> ThreadTouchResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        self.index.thread_touch(
            thread_id=req.thread_id, last_active_at=int(time.time()),
        )
        return ThreadTouchResponse(ok=True)

    def _do_thread_fork(self, req: ThreadForkRequest) -> ThreadForkResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        now = int(time.time())
        result = self.index.thread_fork(
            new_thread_id=req.new_thread_id,
            source_thread_id=req.source_thread_id,
            bot=req.bot, user_id=int(req.user_id),
            title=req.title, created_at=now,
            fork_point_turn_id=req.fork_point_turn_id,
        )
        if result["created"] == 0:
            # thread_id collided — fork must never silently merge into
            # an existing thread (it'd mix two histories).
            raise ValueError(
                f"thread_fork: id {req.new_thread_id!r} already exists",
            )
        return ThreadForkResponse(
            ok=True, thread_id=req.new_thread_id,
            rows_copied=int(result["copied"]),
        )

    def _do_chat_pin(self, req: ChatPinRequest) -> ChatPinResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        rows = self.index.chat_pin_set(
            turn_id=req.turn_id, bot=req.bot, user_id=int(req.user_id),
            pinned=bool(req.pinned),
        )
        return ChatPinResponse(ok=True, rows=rows)

    def _do_entity_page_update(
        self, req: EntityPageUpdateRequest,
    ) -> EntityPageUpdateResponse:
        if self.index is None:
            raise RuntimeError("VaultIndex not initialized — call start() first")
        result = self.index.entity_page_upsert(
            slug=req.slug, kind=req.kind, title=req.title,
            compiled_truth=req.compiled_truth,
            timeline_entry=req.timeline_entry,
            now_epoch=int(time.time()),
            relationships=list(req.relationships) if req.relationships else None,
        )
        return EntityPageUpdateResponse(
            ok=True,
            prior_compiled_truth=str(result.get("prior_compiled_truth") or ""),
            prior_existed=bool(result.get("prior_existed")),
        )

    # =========================================================== git loop

    async def _commit_loop(self) -> None:
        interval = max(1, self._config.gitea.batch_window_seconds)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._flush_commit()
        except asyncio.CancelledError:
            raise

    async def _flush_commit(self) -> None:
        if not self._dirty:
            return
        vault = self._config.vault_path

        # Take a snapshot — only clear _dirty if the flush succeeds, so a
        # failed push doesn't silently drop writes from the next window.
        env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
        env["GIT_TERMINAL_PROMPT"] = "0"
        remote_configured = bool(self._config.gitea.remote)

        def _run() -> bool:
            try:
                subprocess.run(
                    ["git", "add", "-A"], cwd=vault, env=env, check=True,
                    capture_output=True, timeout=30,
                )
                status = subprocess.run(
                    ["git", "status", "--porcelain"], cwd=vault, env=env,
                    check=True, capture_output=True, text=True, timeout=15,
                )
                if not status.stdout.strip():
                    return True  # nothing to do — no re-arm needed
                subprocess.run(
                    ["git", "commit", "-m", "chore(vault): batched learn writes"],
                    cwd=vault, env=env, check=True, capture_output=True, timeout=30,
                )
                if remote_configured:
                    subprocess.run(
                        ["git", "push"], cwd=vault, env=env, check=True,
                        capture_output=True, timeout=60,
                    )
                return True
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode("utf-8", "replace")
                # Redact credential-in-URL patterns before logging.
                import re as _re
                stderr = _re.sub(r"https?://[^:@\s]+:[^@\s]+@", "https://<REDACTED>@", stderr)
                log.warning("git flush failed: %s\n%s", e.cmd, stderr)
                return False
            except subprocess.TimeoutExpired as e:
                log.warning("git flush timed out: %s", e.cmd)
                return False

        ok = await asyncio.to_thread(_run)
        if ok:
            self._dirty = False  # only on success

    # ======================================================= client dispatch

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        expected_token = self._config.auth.token()
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=30.0)
            except asyncio.TimeoutError:
                writer.write(encode_response(ErrorResponse(error="read timeout")))
                await writer.drain()
                return
            if not line:
                return
            try:
                req = decode_request(line, expected_token=expected_token)
            except AuthRequired as e:
                writer.write(encode_response(ErrorResponse(error=str(e))))
                await writer.drain()
                return
            except ValueError as e:
                writer.write(encode_response(ErrorResponse(error=str(e))))
                await writer.drain()
                return

            if isinstance(req, PingRequest):
                writer.write(encode_response(
                    PingResponse(pong=True, daemon_version=__version__)
                ))
                await writer.drain()
            elif isinstance(req, LearnRequest):
                try:
                    resp = await self._do_learn(req)
                except ValueError as e:
                    writer.write(encode_response(ErrorResponse(error=str(e))))
                    await writer.drain()
                    return
                except Exception as e:  # noqa: BLE001
                    log.exception("learn failed")
                    writer.write(encode_response(ErrorResponse(error=f"internal: {e}")))
                    await writer.drain()
                    return
                writer.write(encode_response(resp))
                await writer.drain()
            elif isinstance(req, ChatLogAppendRequest):
                try:
                    resp = self._do_chat_log_append(req)
                except ValueError as e:
                    writer.write(encode_response(ErrorResponse(error=str(e))))
                    await writer.drain()
                    return
                except Exception as e:  # noqa: BLE001
                    log.exception("chat_log_append failed")
                    writer.write(encode_response(ErrorResponse(error=f"internal: {e}")))
                    await writer.drain()
                    return
                writer.write(encode_response(resp))
                await writer.drain()
            elif isinstance(req, ChatLogClearRequest):
                try:
                    resp = self._do_chat_log_clear(req)
                except Exception as e:  # noqa: BLE001
                    log.exception("chat_log_clear failed")
                    writer.write(encode_response(ErrorResponse(error=f"internal: {e}")))
                    await writer.drain()
                    return
                writer.write(encode_response(resp))
                await writer.drain()
            elif isinstance(req, (
                ThreadCreateRequest, ThreadArchiveRequest,
                ThreadSetTitleRequest, ThreadTouchRequest,
                ThreadForkRequest,
                ThreadRenameRequest, ThreadUnarchiveRequest, ThreadPinRequest,
                ChatPinRequest,
                EntityPageUpdateRequest,
            )):
                # Same shape for every thread/pin/entity RPC: dispatch to
                # the matching handler, reply with response or ErrorResponse.
                handlers: dict = {
                    ThreadCreateRequest: self._do_thread_create,
                    ThreadArchiveRequest: self._do_thread_archive,
                    ThreadUnarchiveRequest: self._do_thread_unarchive,
                    ThreadRenameRequest: self._do_thread_rename,
                    ThreadPinRequest: self._do_thread_pin,
                    ThreadSetTitleRequest: self._do_thread_set_title,
                    ThreadTouchRequest: self._do_thread_touch,
                    ThreadForkRequest: self._do_thread_fork,
                    ChatPinRequest: self._do_chat_pin,
                    EntityPageUpdateRequest: self._do_entity_page_update,
                }
                try:
                    resp = handlers[type(req)](req)
                except ValueError as e:
                    writer.write(encode_response(ErrorResponse(error=str(e))))
                    await writer.drain()
                    return
                except Exception as e:  # noqa: BLE001
                    log.exception("thread/pin/entity RPC failed")
                    writer.write(encode_response(
                        ErrorResponse(error=f"internal: {e}"),
                    ))
                    await writer.drain()
                    return
                writer.write(encode_response(resp))
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


# Re-export for tests that still import from here.
__all__ = ["Daemon", "EmbedderLike", "MAX_BODY_CHARS", "MAX_TITLE_CHARS", "scrub_secrets"]
