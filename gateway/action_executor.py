"""ActionExecutor — run synthesis actions emitted by the hive.

The synthesizer outputs a list of `{verb, payload}` actions
(vault_learn, image_render, ntfy_push, create_skill,
image_build_update). The HiveCoordinator delegates execution here so
the coordinator stays focused on dispatch + budget, and so tests can
substitute fakes.

All execution is best-effort: failures land in the returned receipt as
`error`, never raise. The coordinator emits a `synthesis` event with
the receipts so the user can see what actually happened.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from gateway.image_catalog import ASPECT_RATIOS as _ASPECT_DIMS

log = logging.getLogger("gateway.action_executor")


from shared.categories import CATEGORY_FOLDER as _CATEGORY_FOLDER

# Token-Jaccard threshold above which two slugs are treated as the same
# topic and the daemon should merge into the existing note instead of
# creating a near-duplicate. 0.55 catches "kraken-star-citizen-ship"
# vs "kraken-star-citizen-spaceship" (Jaccard 0.6) but leaves "kraken-
# cryptocurrency-exchange" vs "kraken-star-citizen-ship" (0.14) alone.
_DEDUP_JACCARD = 0.55
_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Allowlist for entity_page_update slug values: lowercase alphanumeric,
# hyphens, and underscores only, 1–80 chars. Rejects path-traversal
# sequences, whitespace, JSON markers, and overly-long inputs.
_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,80}$")


from shared.slug_utils import title_from_slug as _title_from_slug

_WIKILINK_SPAN_RE = re.compile(r"\[\[[^\]]+\]\]")


def _split_outside_wikilinks(text: str) -> list[tuple[bool, str]]:
    """Split text into (is_link, span) pairs preserving order so caller
    can rewrite only the non-link spans without disturbing existing
    `[[Foo]]` links."""
    out: list[tuple[bool, str]] = []
    last = 0
    for m in _WIKILINK_SPAN_RE.finditer(text):
        if m.start() > last:
            out.append((False, text[last:m.start()]))
        out.append((True, m.group(0)))
        last = m.end()
    if last < len(text):
        out.append((False, text[last:]))
    return out


def autolink_body(
    body: str, *, vault_path: Path,
    audience: list[str], exclude_title: str,
    max_links: int = 8,
) -> tuple[str, list[str]]:
    """Wrap the first occurrence of any audience-permitted note title
    in `[[Title]]` wikilink syntax. Free-function variant of
    `ActionExecutor._autolink_body` so the user-facing /v1/vault/learn
    route can also auto-link.

    Returns (new_body, linked_titles). Whole-word, case-insensitive
    match; existing `[[...]]` spans are preserved untouched. Titles
    shorter than 4 chars are skipped to avoid over-linking common
    short words. Caps at `max_links` per write.
    """
    if not vault_path.is_dir():
        return body, []
    try:
        from vault_writer.util import audience_matches, parse_frontmatter
    except ImportError:
        return body, []

    def _can_see(note_audience: list[str]) -> bool:
        if not audience:
            return "all" in note_audience
        return any(audience_matches(a, note_audience) for a in audience)

    candidates: list[str] = []
    excl = exclude_title.strip().lower()
    for path in vault_path.rglob("*.md"):
        try:
            rel_parts = path.relative_to(vault_path).parts
        except ValueError:
            continue
        if any(p.startswith(".") for p in rel_parts):
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            fm, _b = parse_frontmatter(raw)
        except (ValueError, KeyError, TypeError):
            # Malformed YAML / unexpected shape — skip this note rather
            # than crashing the whole auto-link pass.
            continue
        if not _can_see(list(fm.get("audience") or ["all"])):
            continue
        # Frontmatter title takes precedence; fall back to a
        # title-cased version of the slug filename. Most notes in this
        # vault don't have a title: field — the daemon doesn't write
        # one — so the slug fallback is the common path.
        title = fm.get("title")
        tt: str
        if isinstance(title, str) and title.strip():
            tt = title.strip()
        else:
            tt = _title_from_slug(path.stem)
        if not tt or tt.lower() == excl or len(tt) < 4:
            continue
        candidates.append(tt)

    if not candidates:
        return body, []
    candidates.sort(key=len, reverse=True)

    linked: list[str] = []
    for tt in candidates:
        if len(linked) >= max_links:
            break
        pattern = re.compile(
            r"(?<![\[\w])" + re.escape(tt) + r"(?![\w\]])",
            re.IGNORECASE,
        )
        spans = _split_outside_wikilinks(body)
        replaced = False
        new_parts: list[str] = []
        for is_link, text in spans:
            if is_link or replaced:
                new_parts.append(text)
                continue
            if pattern.search(text):
                text = pattern.sub(f"[[{tt}]]", text, count=1)
                replaced = True
            new_parts.append(text)
        if replaced:
            body = "".join(new_parts)
            linked.append(tt)

    return body, linked


def _slug_tokens(s: str) -> set[str]:
    return set(_SLUG_TOKEN_RE.findall(s.lower()))


def _slug_jaccard(a: str, b: str) -> float:
    aw, bw = _slug_tokens(a), _slug_tokens(b)
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


class ImageRenderReceiptPayload(TypedDict, total=False):
    """Receipt payload shape for the `image_render` verb. Read by the
    chat WS image bridge in `routes/chat.py` — `job_id` is required
    when the receipt is `ok`, otherwise the bridge can't subscribe to
    the matching `image_done` event."""
    job_id: str
    prompt: str


class EscalateReceiptPayload(TypedDict, total=False):
    """Receipt payload shape for the `escalate_to_dev` verb. Drives
    the Activity-tab badge + `/v1/escalations` route surface."""
    path: str
    severity: str           # "low" | "medium" | "high"


class CreateSkillReceiptPayload(TypedDict, total=False):
    """Receipt payload shape for the `create_skill` verb."""
    name: str
    path: str


class ImageBuildUpdateReceiptPayload(TypedDict, total=False):
    """Receipt payload shape for the `image_build_update` verb."""
    changed: list[str]


@dataclass
class ActionReceipt:
    """Result of running one synthesizer-emitted action.

    `payload` is intentionally `dict[str, Any]` at the dataclass level
    so the receipt list stays serialisable as a single shape. Each
    verb's contract for what's in there is captured by the per-verb
    TypedDicts above — the chat WS image bridge expects the
    `image_render` shape, the Activity tab expects the `escalate_to_dev`
    shape, etc. New verbs should add a TypedDict alongside the others
    so the next reader doesn't have to grep the producer to learn the
    keys.
    """
    verb: str
    ok: bool
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class ActionExecutor:
    def __init__(
        self,
        *,
        vault_client_factory=None,    # () -> VaultClient
        image_shim=None,
        video_shim=None,              # VideoShim for video_render verb
        ntfy=None,
        skill_registry=None,
        image_build_store=None,
        critic_helper=None,           # for create_skill rubric
        rate_limiter=None,            # gateway.rate_limit.RateLimiter
        vault_path=None,              # for vault_forget direct file deletion
        state_dir=None,               # for resolving reference_media_id uploads
        memory_store=None,            # MemoryStore for core_memory_* verbs
        contradiction_detector=None,  # optional EntityContradictionDetector
        composio_client=None,         # optional ComposioClient (Phase B)
    ) -> None:
        self._vault_client_factory = vault_client_factory
        self._image_shim = image_shim
        self._video_shim = video_shim
        self._ntfy = ntfy
        self._skill_registry = skill_registry
        self._image_build_store = image_build_store
        self._critic = critic_helper
        self._rate_limiter = rate_limiter
        self._vault_path = vault_path
        self._state_dir = state_dir
        self._memory_store = memory_store
        self._contradiction_detector = contradiction_detector
        self._composio_client = composio_client

    async def execute_all(
        self,
        actions: list[dict],
        *,
        device_id: str = "",
        device_audience: list[str] | None = None,
        user_id: int = 0,
        thread_id: str = "default",
        bot: str = "terry",
    ) -> list[ActionReceipt]:
        receipts: list[ActionReceipt] = []
        for raw in actions:
            if not isinstance(raw, dict):
                continue
            verb = str(raw.get("verb") or "").strip()
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            try:
                receipt = await self._execute_one(
                    verb, payload,
                    device_id=device_id,
                    device_audience=device_audience,
                    user_id=user_id, thread_id=thread_id, bot=bot,
                )
            except Exception as e:  # noqa: BLE001
                log.exception("action %r raised", verb)
                receipt = ActionReceipt(
                    verb=verb, ok=False,
                    detail=f"{type(e).__name__}: {e}",
                )
            receipts.append(receipt)
        return receipts

    # ---------------------------------------------------------------- per-verb

    async def _execute_one(
        self, verb: str, payload: dict,
        *,
        device_id: str,
        device_audience: list[str] | None,
        user_id: int = 0,
        thread_id: str = "default",
        bot: str = "terry",
    ) -> ActionReceipt:
        if verb == "vault_learn":
            return await self._vault_learn(payload, device_audience, device_id)
        if verb == "image_render":
            return await self._image_render(payload, device_id)
        if verb == "ntfy_push":
            return await self._ntfy_push(payload)
        if verb == "create_skill":
            return await self._create_skill(payload)
        if verb == "escalate_to_dev":
            return await self._escalate_to_dev(payload, device_id)
        if verb == "image_build_update":
            return self._image_build_update(payload, device_id)
        if verb == "vault_forget":
            return await self._vault_forget(payload, device_audience, device_id)
        if verb == "core_memory_replace":
            return self._core_memory_replace(
                payload, user_id=user_id, thread_id=thread_id,
            )
        if verb == "core_memory_append":
            return self._core_memory_append(
                payload, user_id=user_id, thread_id=thread_id,
            )
        if verb == "entity_page_update":
            return await self._entity_page_update(
                payload, device_audience=device_audience,
                user_id=user_id, bot=bot,
            )
        if verb == "run_python":
            return await self._run_python(payload)
        if verb == "generate_doc":
            return await self._generate_doc(payload)
        if verb == "generate_deck":
            return await self._generate_deck(payload)
        if verb == "saas_call":
            return await self._saas_call(payload)
        if verb == "video_render":
            return await self._video_render(payload, device_id)
        if verb == "lora_train":
            return await self._lora_train(payload)
        return ActionReceipt(
            verb=verb, ok=False,
            detail=f"unknown verb: {verb!r}",
        )

    # ---------------------------------------------------------------- sandbox

    async def _run_python(self, payload: dict) -> ActionReceipt:
        from gateway.sandbox.python_runtime import run_python
        code = str(payload.get("code", "")).strip()
        if not code:
            return ActionReceipt(
                verb="run_python", ok=False, detail="missing code",
            )
        if len(code) > 8000:
            return ActionReceipt(
                verb="run_python", ok=False,
                detail=f"code too large: {len(code)} chars (max 8000)",
            )
        timeout_s = float(payload.get("timeout_s") or 15.0)
        timeout_s = min(max(timeout_s, 0.1), 60.0)
        try:
            r = await run_python(code, timeout_s=timeout_s)
        except Exception as e:  # noqa: BLE001
            log.exception("run_python failed")
            return ActionReceipt(
                verb="run_python", ok=False, detail=f"sandbox crashed: {e}",
            )
        if r.timed_out:
            return ActionReceipt(
                verb="run_python", ok=False,
                detail=f"timed out after {timeout_s}s",
                payload={"stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]},
            )
        if not r.ok:
            return ActionReceipt(
                verb="run_python", ok=False, detail=r.error or "exec failed",
                payload={"stdout": r.stdout[-2000:], "stderr": r.stderr[-2000:]},
            )
        detail = r.return_value or (r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "ok")
        return ActionReceipt(
            verb="run_python", ok=True, detail=detail[:400],
            payload={
                "stdout": r.stdout[-4000:],
                "stderr": r.stderr[-2000:],
                "return_value": r.return_value,
                "duration_ms": r.duration_ms,
            },
        )

    # ---------------------------------------------------------------- saas (Phase B)

    async def _saas_call(self, payload: dict) -> ActionReceipt:
        """Composio-backed external SaaS call (Slack, Gmail, etc.).

        Phase B ships this as a critic-gated, key-optional surface. The
        ComposioClient itself never raises — when the SDK or
        COMPOSIO_API_KEY is missing it returns a structured
        `composio_unavailable` result, which we surface to the user as a
        graceful failed receipt rather than a crash.
        """
        if self._composio_client is None:
            return ActionReceipt(
                verb="saas_call", ok=False,
                detail="composio client not configured",
            )
        app = str(payload.get("app", "")).strip()
        action = str(payload.get("action", "")).strip()
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            return ActionReceipt(
                verb="saas_call", ok=False,
                detail="args must be an object",
            )
        if not app:
            return ActionReceipt(
                verb="saas_call", ok=False, detail="missing app",
            )
        if not action:
            return ActionReceipt(
                verb="saas_call", ok=False, detail="missing action",
            )
        try:
            result = await asyncio.to_thread(
                self._composio_client.execute,
                app=app, action=action, args=args,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("saas_call dispatch failed")
            return ActionReceipt(
                verb="saas_call", ok=False,
                detail=f"{type(e).__name__}: {e}",
            )
        if not result.ok:
            return ActionReceipt(
                verb="saas_call", ok=False,
                detail=str(result.error or "saas call failed"),
                payload={"app": app, "action": action, "result": result.result},
            )
        return ActionReceipt(
            verb="saas_call", ok=True,
            detail=f"{app}.{action} ok",
            payload={"app": app, "action": action, "result": result.result},
        )

    # ---------------------------------------------------------------- exporters

    def _safe_slug(self, raw: str, *, fallback: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")
        return (s or fallback)[:60]

    async def _generate_doc(self, payload: dict) -> ActionReceipt:
        title = str(payload.get("title", "")).strip()
        body_md = str(payload.get("body_md", "")).strip()
        if not title:
            return ActionReceipt(
                verb="generate_doc", ok=False, detail="missing title",
            )
        if not body_md:
            return ActionReceipt(
                verb="generate_doc", ok=False, detail="missing body_md",
            )
        if self._state_dir is None:
            return ActionReceipt(
                verb="generate_doc", ok=False, detail="state_dir not configured",
            )
        slug = self._safe_slug(
            str(payload.get("slug") or title), fallback="document",
        )
        out_dir = Path(self._state_dir) / "media" / "docs"
        out_path = out_dir / f"{slug}.docx"
        try:
            from gateway.exporters.doc_builder import build_docx
            written = await asyncio.to_thread(
                build_docx, title, body_md, out_path,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("generate_doc failed")
            return ActionReceipt(
                verb="generate_doc", ok=False,
                detail=f"{type(e).__name__}: {e}",
            )
        size = written.stat().st_size if written.exists() else 0
        return ActionReceipt(
            verb="generate_doc", ok=True,
            detail=f"wrote {written.name} ({size} bytes)",
            payload={"path": str(written), "size_bytes": size, "slug": slug},
        )

    async def _generate_deck(self, payload: dict) -> ActionReceipt:
        title = str(payload.get("title", "")).strip()
        sections = payload.get("sections")
        if not title:
            return ActionReceipt(
                verb="generate_deck", ok=False, detail="missing title",
            )
        if not isinstance(sections, list) or not sections:
            return ActionReceipt(
                verb="generate_deck", ok=False,
                detail="sections must be non-empty list",
            )
        if self._state_dir is None:
            return ActionReceipt(
                verb="generate_deck", ok=False, detail="state_dir not configured",
            )
        slug = self._safe_slug(
            str(payload.get("slug") or title), fallback="deck",
        )
        subtitle = payload.get("subtitle")
        subtitle = str(subtitle).strip() if subtitle else None
        out_dir = Path(self._state_dir) / "media" / "decks"
        out_path = out_dir / f"{slug}.pptx"
        try:
            from gateway.exporters.slide_builder import build_pptx
            written = await asyncio.to_thread(
                build_pptx, title, sections, out_path, subtitle=subtitle,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("generate_deck failed")
            return ActionReceipt(
                verb="generate_deck", ok=False,
                detail=f"{type(e).__name__}: {e}",
            )
        size = written.stat().st_size if written.exists() else 0
        return ActionReceipt(
            verb="generate_deck", ok=True,
            detail=f"wrote {written.name} ({size} bytes, {len(sections)} sections)",
            payload={
                "path": str(written), "size_bytes": size, "slug": slug,
                "section_count": len(sections),
            },
        )

    # ---------------------------------------------------------------- core mem

    def _core_memory_replace(
        self, payload: dict, *, user_id: int, thread_id: str,
    ) -> ActionReceipt:
        if self._memory_store is None:
            return ActionReceipt(
                verb="core_memory_replace", ok=False,
                detail="memory store not configured",
            )
        slot = str(payload.get("slot", "")).strip()
        content = str(payload.get("content", ""))
        if not slot:
            return ActionReceipt(
                verb="core_memory_replace", ok=False,
                detail="missing slot name",
            )
        if not user_id:
            return ActionReceipt(
                verb="core_memory_replace", ok=False,
                detail="missing user_id",
            )
        self._memory_store.set_core_slot(
            user_id, thread_id=thread_id, name=slot, content=content,
        )
        return ActionReceipt(
            verb="core_memory_replace", ok=True,
            detail=f"set slot {slot!r} ({len(content)} chars)",
            payload={"slot": slot, "thread_id": thread_id},
        )

    def _core_memory_append(
        self, payload: dict, *, user_id: int, thread_id: str,
    ) -> ActionReceipt:
        if self._memory_store is None:
            return ActionReceipt(
                verb="core_memory_append", ok=False,
                detail="memory store not configured",
            )
        slot = str(payload.get("slot", "")).strip()
        content = str(payload.get("content", ""))
        if not slot or not content:
            return ActionReceipt(
                verb="core_memory_append", ok=False,
                detail="need slot + content",
            )
        if not user_id:
            return ActionReceipt(
                verb="core_memory_append", ok=False,
                detail="missing user_id",
            )
        self._memory_store.append_core_slot(
            user_id, thread_id=thread_id, name=slot, content=content,
        )
        return ActionReceipt(
            verb="core_memory_append", ok=True,
            detail=f"appended to slot {slot!r}",
            payload={"slot": slot, "thread_id": thread_id},
        )

    # ---------------------------------------------------------------- entities

    async def _entity_page_update(
        self, payload: dict, *,
        device_audience: list[str] | None,
        user_id: int, bot: str,
    ) -> ActionReceipt:
        """Upsert an entity_page row; append to its timeline. Used by
        the synthesizer to record stable facts about people, projects,
        and concepts across threads. Audience-clamped via shared.audience.
        """
        if self._vault_client_factory is None:
            return ActionReceipt(
                verb="entity_page_update", ok=False,
                detail="vault client not configured",
            )
        slug = str(payload.get("id") or payload.get("slug") or "").strip()
        kind = str(payload.get("kind", "concept")).strip() or "concept"
        title = str(payload.get("title", "")).strip()
        compiled_truth = str(payload.get("compiled_truth", "")).strip()
        timeline_entry = str(payload.get("timeline_entry", "")).strip()
        if not _SLUG_RE.match(slug):
            return ActionReceipt(
                verb="entity_page_update", ok=False,
                detail="invalid slug — must match ^[a-z0-9_-]{1,80}$",
            )
        if not title:
            return ActionReceipt(
                verb="entity_page_update", ok=False,
                detail="need id (slug) + title",
            )
        if kind not in ("person", "project", "concept", "thing"):
            kind = "concept"
        # Phase 3 (#456): graphify-shaped edge list. Validate at the
        # executor edge — the protocol layer also validates, but a
        # rejected verb here gives the synthesizer a friendly receipt
        # detail rather than an opaque RPC failure.
        rels_raw = payload.get("relationships")
        relationships: list[dict] | None
        if rels_raw is None:
            relationships = None
        elif not isinstance(rels_raw, list):
            return ActionReceipt(
                verb="entity_page_update", ok=False,
                detail="relationships must be a list",
            )
        else:
            _ALLOWED_CONFIDENCE = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
            if len(rels_raw) > 32:
                return ActionReceipt(
                    verb="entity_page_update", ok=False,
                    detail=f"too many relationships ({len(rels_raw)} > 32)",
                )
            cleaned: list[dict] = []
            for edge in rels_raw:
                if not isinstance(edge, dict):
                    return ActionReceipt(
                        verb="entity_page_update", ok=False,
                        detail="relationships entry must be an object",
                    )
                tgt = edge.get("target_slug")
                label = edge.get("label")
                conf = edge.get("confidence")
                if (not isinstance(tgt, str) or not tgt
                        or not isinstance(label, str) or not label):
                    return ActionReceipt(
                        verb="entity_page_update", ok=False,
                        detail="edge needs target_slug + label",
                    )
                if conf not in _ALLOWED_CONFIDENCE:
                    return ActionReceipt(
                        verb="entity_page_update", ok=False,
                        detail=(
                            f"edge confidence must be one of "
                            f"{_ALLOWED_CONFIDENCE}, got {conf!r}"
                        ),
                    )
                cleaned.append({
                    "target_slug": tgt[:80], "label": label[:80],
                    "confidence": conf,
                })
            relationships = cleaned
        client = self._vault_client_factory()
        try:
            resp = await client.entity_page_update(
                slug=slug, kind=kind, title=title,
                compiled_truth=compiled_truth,
                timeline_entry=timeline_entry,
                relationships=relationships,
            )
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(
                verb="entity_page_update", ok=False, detail=str(e),
            )
        if not resp or not resp.get("ok"):
            return ActionReceipt(
                verb="entity_page_update", ok=False,
                detail="vault_writer rejected the update",
            )
        # Optional contradiction detection — fires only when the
        # detector is wired (Config.feature_contradiction_detection on).
        if (
            self._contradiction_detector is not None
            and compiled_truth
            and resp.get("prior_compiled_truth")
        ):
            try:
                await self._contradiction_detector.check(
                    slug=slug, title=title,
                    prior=str(resp.get("prior_compiled_truth") or ""),
                    new=compiled_truth,
                    bot=bot, device_audience=device_audience,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("contradiction check failed: %s", e)
        return ActionReceipt(
            verb="entity_page_update", ok=True,
            detail=f"updated entity {slug!r}",
            payload={"slug": slug, "kind": kind},
        )

    async def _vault_learn(
        self, payload: dict, device_audience: list[str] | None,
        device_id: str = "",
    ) -> ActionReceipt:
        if self._vault_client_factory is None:
            return ActionReceipt(verb="vault_learn", ok=False,
                                 detail="vault client not configured")
        # Rate-limit synthesis-emitted vault writes so a runaway hive
        # turn can't flood the vault.
        if (
            self._rate_limiter is not None
            and device_id
            and not self._rate_limiter.try_acquire(device_id, "vault_actions")
        ):
            return ActionReceipt(
                verb="vault_learn", ok=False,
                detail="rate limit: too many vault writes; backing off",
            )
        # Field aliases: the synthesizer LLM occasionally emits `kind`
        # for category and `body_md` for body (matches its own internal
        # markdown shape). The prompt asks for category/title/body, but
        # rejecting on the alias gives a confident "saved" reply for a
        # silently-failed write. Coerce defensively.
        category = str(
            payload.get("category") or payload.get("kind") or ""
        ).strip()
        title = str(payload.get("title", "")).strip()
        body = str(
            payload.get("body") or payload.get("body_md") or ""
        ).strip()
        if not (category and title and body):
            return ActionReceipt(
                verb="vault_learn", ok=False,
                detail="missing category/title/body",
            )
        # Quality gate: refuse below-threshold writes so the index
        # doesn't fill up with stubs and link-lists. The gate is a
        # cheap deterministic check — not an LLM call.
        # Skip when `_server_derived` is set: the coordinator built
        # the payload directly from the user's literal phrasing, so
        # the user IS the quality signal. The gate exists to catch
        # LLM-stub writes, not user-authored facts.
        if not payload.get("_server_derived"):
            from gateway.vault_quality import evaluate as _qa_eval
            verdict = _qa_eval(title=title, body=body, category=category)
            if not verdict.ok:
                log.info(
                    "vault_learn rejected: %s (title=%r, body[:60]=%r)",
                    verdict.reason, title, body[:60],
                )
                return ActionReceipt(
                    verb="vault_learn", ok=False,
                    detail=f"below quality threshold: {verdict.reason}",
                )
        # Audience clamping. Single source of truth at `shared.audience`
        # so a tightening fix doesn't have to land in two places.
        from shared.audience import clamp_audience
        audience = clamp_audience(
            payload.get("audience") or ["terry", "claude-code"],
            device_audience,
        )
        tags = list(payload.get("tags") or [])
        extra = dict(payload.get("extra") or {})
        # Auto-pull sources/corroboration up from payload root if present.
        if "sources" in payload and "sources" not in extra:
            extra["sources"] = payload["sources"]
        if "corroboration" in payload and "corroboration" not in extra:
            extra["corroboration"] = payload["corroboration"]

        # Dedup: if a recent note in the same category folder has a
        # near-identical slug (token Jaccard ≥ 0.55), reuse its title so
        # the daemon merges into the existing file instead of creating a
        # near-duplicate. Reproduction (2026-04-28 turn log): two
        # consecutive turns about "Kraken" wrote
        # `kraken-star-citizen-ship.md` and
        # `kraken-star-citizen-spaceship.md` — same entity, two files.
        merged_into: str | None = None
        existing_title = self._find_similar_existing_title(
            category, title,
            server_derived=bool(payload.get("_server_derived")),
        )
        if existing_title and existing_title != title:
            log.info(
                "vault_learn: merging '%s' into existing note '%s' (Jaccard ≥ %.2f)",
                title, existing_title, _DEDUP_JACCARD,
            )
            merged_into = title
            title = existing_title

        # Auto-link: scan the body for mentions of titles of OTHER
        # existing notes (audience-permitted) and wrap the first
        # occurrence in [[wikilink]]. Lets Terry "smart link where
        # possible when adding to the vault" without requiring the
        # synthesizer to emit a separate link verb. Idempotent —
        # re-running over already-linked text is a no-op.
        body, linked_titles = self._autolink_body(
            body, category=category, audience=audience, exclude_title=title,
        )

        client = self._vault_client_factory()
        try:
            resp = await client.learn(
                author=payload.get("author", "terry"),
                category=category, title=title, body=body,
                audience=audience, tags=tags, extra=extra,
            )
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(verb="vault_learn", ok=False,
                                 detail=str(e))
        if resp and resp.get("ok"):
            detail = f"saved {resp.get('path', '?')}"
            if merged_into is not None:
                detail = f"merged into {resp.get('path', '?')} (was '{merged_into}')"
            if linked_titles:
                detail = f"{detail}; linked: {', '.join(linked_titles)}"
            receipt_payload = {"path": resp.get("path", ""), "title": title}
            if merged_into is not None:
                receipt_payload["merged_from_title"] = merged_into
            if linked_titles:
                receipt_payload["linked_titles"] = list(linked_titles)
            return ActionReceipt(
                verb="vault_learn", ok=True,
                detail=detail,
                payload=receipt_payload,
            )
        # Surface the daemon's actual error so the turn-log + UI shows
        # something useful (rejected category, schema fail, etc.).
        if resp:
            err = (
                resp.get("error")
                or resp.get("detail")
                or resp.get("message")
                or "daemon returned ok=false with no error field"
            )
            return ActionReceipt(
                verb="vault_learn", ok=False,
                detail=f"daemon rejected: {err}"[:300],
            )
        return ActionReceipt(
            verb="vault_learn", ok=False,
            detail="vault_writer unreachable",
        )

    def _autolink_body(
        self, body: str, *, category: str,
        audience: list[str], exclude_title: str,
    ) -> tuple[str, list[str]]:
        """Instance wrapper around `autolink_body` using self._vault_path."""
        if self._vault_path is None:
            return body, []
        return autolink_body(
            body, vault_path=self._vault_path,
            audience=audience, exclude_title=exclude_title,
        )

    def _find_similar_existing_title(
        self, category: str, new_title: str,
        *, server_derived: bool = False,
    ) -> str | None:
        """Look for an existing note in the same category folder with a
        slug-token Jaccard ≥ _DEDUP_JACCARD. Returns the canonical title
        from the existing note's frontmatter, or None if nothing close.

        Best-effort and side-effect-free: any IO error returns None.
        Categories that mutate via append (journal/session/person) are
        skipped — those have their own merge semantics.

        When ``server_derived`` is True, single-token titles ARE
        considered (e.g. user said "update the Vanduul note" → we want
        to merge into "Faction — Vanduul" instead of creating a new
        "vanduul.md"). The match strategy then falls through to a
        substring check against existing titles.
        """
        if self._vault_path is None:
            return None
        folder_name = _CATEGORY_FOLDER.get(category)
        if folder_name is None:
            return None
        # Skip append-style categories. Daemon already merges those.
        if category in ("journal", "session", "person"):
            return None
        folder = self._vault_path / folder_name
        if not folder.is_dir():
            return None

        new_tokens = _slug_tokens(new_title)
        if len(new_tokens) < 2 and not server_derived:
            # Single-token titles are too noisy to dedup safely.
            return None

        # Lazy import: vault_writer.util is fine to depend on at call
        # time (we already do for vault_forget's audience reads), but
        # not at module import time.
        try:
            from vault_writer.util import parse_frontmatter
        except Exception:
            parse_frontmatter = None  # type: ignore[assignment]

        best: tuple[float, str] | None = None
        new_first = next(iter(_SLUG_TOKEN_RE.findall(new_title.lower())), "")
        for path in folder.rglob("*.md"):
            if any(p.startswith(".") for p in path.relative_to(folder).parts):
                continue
            stem_score = _slug_jaccard(new_title, path.stem)
            # Server-derived single-token path: also accept notes whose
            # stem CONTAINS the new title token as a whole word. Catches
            # "vanduul" matching "faction-vanduul" — the same entity by
            # any reasonable reading.
            if stem_score < _DEDUP_JACCARD:
                if not server_derived or len(new_tokens) >= 2:
                    continue
                stem_tokens = path.stem.lower().split("-")
                if new_first not in stem_tokens:
                    continue
                # Synthesize a substring-equivalent score so the best-
                # match comparison still works.
                stem_score = _DEDUP_JACCARD
            else:
                # Require first token (slug "head") to match — guards
                # against e.g. "kraken-..." ↔ "krakatoa-..." which share
                # "kra*" prefix but are different topics. Skipped for
                # single-token server-derived above (the token IS the
                # head).
                stem_first = next(iter(path.stem.split("-")), "")
                if new_first and stem_first and new_first != stem_first:
                    # Server-derived single-token can match even when
                    # the new title isn't the leading token in the stem.
                    if not (server_derived and len(new_tokens) < 2):
                        continue
            # Read the frontmatter to get the canonical title. Falling
            # back to a title-cased slug when FM has no title field
            # (most notes in this vault — the daemon doesn't write one).
            canonical = _title_from_slug(path.stem)
            if parse_frontmatter is not None:
                try:
                    raw = path.read_text(encoding="utf-8")
                    fm, _body = parse_frontmatter(raw)
                    title = fm.get("title")
                    if isinstance(title, str) and title.strip():
                        canonical = title.strip()
                except Exception:
                    pass
            if best is None or stem_score > best[0]:
                best = (stem_score, canonical)

        return best[1] if best else None

    async def _vault_forget(
        self, payload: dict, device_audience: list[str] | None,
        device_id: str = "",
    ) -> ActionReceipt:
        """Delete one or more vault notes by path or by query.

        Payload shapes accepted:
          {"paths": ["knowledge/2026/04/foo.md", ...]}  — explicit paths
          {"query": "drake cutlass"}                     — match titles
            (deletes any note whose path basename or frontmatter title
            contains the query, case-insensitive)

        Files are hard-deleted. Git history preserves them in the
        vault repo, so a hand-edit can recover.
        """
        from pathlib import Path
        import re

        # Same rate bucket as vault_learn — writes and deletes both
        # mutate the vault.
        if (
            self._rate_limiter is not None and device_id
            and not self._rate_limiter.try_acquire(device_id, "vault_actions")
        ):
            return ActionReceipt(
                verb="vault_forget", ok=False,
                detail="rate limited: vault_actions",
            )

        vault_path = self._vault_path
        if vault_path is None:
            return ActionReceipt(
                verb="vault_forget", ok=False,
                detail="vault path not configured",
            )

        paths_arg = payload.get("paths")
        query = (payload.get("query") or "").strip().lower()
        if not paths_arg and not query:
            return ActionReceipt(
                verb="vault_forget", ok=False,
                detail="payload must include 'paths' or 'query'",
            )

        # Write-targetable subfolders. canon/ and ops/ are deliberately
        # excluded: canon is human-only ground truth, ops is the
        # escalate_to_dev queue — neither should ever be deletable by a
        # synthesizer-emitted forget action, even if a prompt-injected
        # note tries to point at them.
        _writable_subdirs = ("knowledge", "journals", "sessions", "references")
        vault_root = vault_path.resolve()

        def _within_writable(resolved: Path) -> bool:
            try:
                rel = resolved.relative_to(vault_root)
            except ValueError:
                return False
            parts = rel.parts
            return bool(parts) and parts[0] in _writable_subdirs

        targets: list[Path] = []
        if isinstance(paths_arg, list):
            for p in paths_arg:
                if not isinstance(p, str) or not p:
                    continue
                # Reject Windows Alternate Data Streams: `foo.md:secret`
                # has suffix `.md` but unlink() would scrub only the
                # named stream, leaving a misleading success receipt.
                if ":" in Path(p).name:
                    continue
                # Confine to the vault — refuse anything that resolves
                # outside, and refuse anything that lands in canon/ or
                # ops/ (the query branch already enforces this; mirror
                # it here so a path-targeted forget can't escape the
                # allowlist).
                resolved = (vault_path / p).resolve()
                if not _within_writable(resolved):
                    continue
                if resolved.is_file() and resolved.suffix == ".md":
                    targets.append(resolved)
        if query:
            # Walk knowledge / journals / sessions / references — the
            # write-targetable folders. Never touch canon/ (human-only).
            search_dirs = [
                vault_path / sub for sub in _writable_subdirs
            ]
            # Tokenize: keep only word chars (3+), drop separators like
            # em-dash and the literal word "note" which shows up in many
            # filenames and would over-match. Stop-words filtered so
            # "the X note" doesn't match every file. SC-edit eval
            # (2026-06-05) showed "Faction — Nine Tails" failing to
            # match "faction-nine-tails.md" because the em-dash token
            # was required to appear in the filename.
            _stop = {"the", "and", "for", "with", "from", "this", "that"}
            raw_tokens = re.findall(r"[A-Za-z0-9]+", query.lower())
            tokens = [t for t in raw_tokens if len(t) >= 2 and t not in _stop]
            if not tokens:
                tokens = raw_tokens  # fall back to anything we have
            for d in search_dirs:
                if not d.is_dir():
                    continue
                for p in d.rglob("*.md"):
                    # Normalize filename to alphanumerics so hyphens in
                    # the slug don't block matches like "nine tails".
                    # Use full p.name so existing callers that pattern-
                    # match on ".md" continue to work.
                    name_norm = re.sub(
                        r"[^A-Za-z0-9]+", " ", p.name.lower(),
                    )
                    if all(t in name_norm for t in tokens):
                        targets.append(p)

        # SECURITY: only delete notes whose frontmatter `audience`
        # field intersects the requesting device's audience. Without
        # this, a Claude-Code-only device's synthesizer could query
        # for "favourite color" and wipe Terry's saved notes (or vice
        # versa). When audience parsing fails we fail-closed (skip).
        if device_audience:
            allowed = set(device_audience)
            audience_filtered: list[Path] = []
            for p in targets:
                aud = _read_audience(p)
                if aud is None:
                    continue
                if "all" in aud or allowed.intersection(aud):
                    audience_filtered.append(p)
            targets = audience_filtered

        # Dedupe and cap (safety: never delete more than 20 in one
        # action so a runaway turn can't nuke the vault).
        unique = []
        seen = set()
        for p in targets:
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
            if len(unique) >= 20:
                break

        if not unique:
            return ActionReceipt(
                verb="vault_forget", ok=False,
                detail=f"no matching notes for query={query!r} paths={paths_arg!r}",
            )

        deleted: list[str] = []
        errors: list[str] = []
        for p in unique:
            try:
                p.unlink()
                deleted.append(str(p.relative_to(vault_path)).replace("\\", "/"))
            except OSError as e:
                errors.append(f"{p.name}: {e}")

        if not deleted:
            return ActionReceipt(
                verb="vault_forget", ok=False,
                detail=f"no files removed (errors: {errors})",
            )
        detail = f"removed {len(deleted)} note(s): " + ", ".join(deleted[:3])
        if len(deleted) > 3:
            detail += f" (+{len(deleted) - 3} more)"
        if errors:
            detail += f" (errors: {len(errors)})"
        return ActionReceipt(
            verb="vault_forget", ok=True, detail=detail[:500],
        )

    async def _image_render(
        self, payload: dict, device_id: str,
    ) -> ActionReceipt:
        if self._image_shim is None:
            return ActionReceipt(verb="image_render", ok=False,
                                 detail="image shim not configured")
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return ActionReceipt(verb="image_render", ok=False,
                                 detail="missing prompt")
        kwargs = {
            "prompt": prompt,
            "count": int(payload.get("count", 1)),
            "negative_prompt": str(payload.get("negative_prompt", "")),
        }
        # Aspect → width/height mapping.
        aspect = str(payload.get("aspect", "portrait")).lower()
        kwargs["width"], kwargs["height"] = _aspect_dims(aspect)
        if "loras" in payload:
            kwargs["lora_overrides"] = list(payload["loras"])
        # SECURITY: an LLM-emitted `reference_path` is an arbitrary
        # filesystem read (image_shim.enqueue → generate_img2img_fn
        # → PIL.open). The synthesizer is the producer here and it
        # ingests untrusted web text — refuse the field outright.
        # Img2img must come from a media_id resolved through
        # _resolve_uploaded_reference (route-level only).
        if "reference_path" in payload:
            return ActionReceipt(
                verb="image_render", ok=False,
                detail=(
                    "reference_path is not allowed in synthesizer-emitted "
                    "image_render actions; use reference_media_id via the "
                    "/v1/images/upload + chat WS path instead"
                ),
            )
        if payload.get("reference_media_id"):
            mid = str(payload["reference_media_id"])
            # Resolve by media_id only — confined to state_dir/media-uploads
            # by resolve_uploaded_reference (alphanum-id, fixed extensions).
            # Lives in gateway.media_paths so this core-layer file doesn't
            # have to import the routes layer (cycle the architect flagged).
            from gateway.media_paths import resolve_uploaded_reference
            if self._state_dir is None:
                return ActionReceipt(
                    verb="image_render", ok=False,
                    detail="state_dir not configured for reference media",
                )
            ref = resolve_uploaded_reference(self._state_dir, mid)
            if ref is None:
                return ActionReceipt(
                    verb="image_render", ok=False,
                    detail=f"unknown reference_media_id: {mid}",
                )
            kwargs["reference_path"] = str(ref)
            kwargs["strength"] = float(payload.get("strength", 0.6))
        try:
            job = await self._image_shim.enqueue(**kwargs)
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(verb="image_render", ok=False,
                                 detail=str(e))
        # Clear the per-device build state once we've actually queued.
        if self._image_build_store is not None and device_id:
            self._image_build_store.clear(device_id)
        return ActionReceipt(
            verb="image_render", ok=True,
            detail=f"queued job {job.id}",
            # Include the prompt so the chat-handler's image_pending
            # bridge can show it on the pending bubble while the
            # render is in flight.
            payload={"job_id": job.id, "prompt": kwargs.get("prompt", "")},
        )

    async def _video_render(
        self, payload: dict, device_id: str,
    ) -> ActionReceipt:
        """Enqueue a WAN image-to-video render job via VideoShim.

        Mirrors `_image_render` exactly — enqueue is non-blocking; the
        job result is delivered via the event bus `video_done` event
        (same channel the standalone /v1/videos route uses).

        Required payload fields:
          - `prompt`           — text description of the video motion
          - `seed_image_path`  — path to the seed image (full filesystem path
                                 or a media_id resolved from state_dir/media/)

        Optional payload fields mirror VideoShim.enqueue kwargs:
          - `negative_prompt`, `width`, `height`, `num_frames`,
            `fps`, `seed`, `num_steps`, `guidance_scale`,
            `lora_path`, `lora_strength`
        """
        if self._video_shim is None:
            return ActionReceipt(
                verb="video_render", ok=False,
                detail="video shim not configured",
            )
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return ActionReceipt(
                verb="video_render", ok=False,
                detail="missing prompt",
            )
        seed_image_path = str(payload.get("seed_image_path", "")).strip()
        if not seed_image_path:
            return ActionReceipt(
                verb="video_render", ok=False,
                detail="missing seed_image_path",
            )
        kwargs: dict = {
            "prompt": prompt,
            "seed_image_path": seed_image_path,
        }
        for key in (
            "negative_prompt", "width", "height", "num_frames",
            "fps", "seed", "num_steps", "guidance_scale",
            "lora_path", "lora_strength",
        ):
            if key in payload:
                kwargs[key] = payload[key]
        try:
            job = await self._video_shim.enqueue(**kwargs)
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(
                verb="video_render", ok=False,
                detail=str(e),
            )
        return ActionReceipt(
            verb="video_render", ok=True,
            detail=f"queued video job {job.id}",
            payload={"job_id": job.id, "prompt": prompt},
        )

    async def _lora_train(self, payload: dict) -> ActionReceipt:
        """Enqueue a LoRA training job via the asset importer's training path.

        The verb is intentionally lightweight: it enqueues a job and
        returns immediately so the synthesizer turn is not blocked by a
        GPU-heavy training run (which can take minutes to hours).

        Required payload fields:
          - `dataset_path`  — path to training images directory
          - `output_name`   — name for the output LoRA file (no extension)

        Optional:
          - `base_model`    — model checkpoint to fine-tune (default: FLUX)
          - `steps`         — training steps (default: 500, max: 2000)
          - `learning_rate` — float (default: 0.0001)

        The training pipeline is gated: if the imageToVideo LoRA trainer
        is not available (C:\\Projects\\imageToVideo not present), returns
        a graceful `ok=False` receipt rather than crashing.
        """
        dataset_path = str(payload.get("dataset_path", "")).strip()
        output_name = str(payload.get("output_name", "")).strip()
        if not dataset_path:
            return ActionReceipt(
                verb="lora_train", ok=False,
                detail="missing dataset_path",
            )
        if not output_name:
            return ActionReceipt(
                verb="lora_train", ok=False,
                detail="missing output_name",
            )
        # Validate output_name: alphanumeric + hyphens/underscores only.
        import re as _re
        if not _re.match(r"^[a-zA-Z0-9_-]{1,80}$", output_name):
            return ActionReceipt(
                verb="lora_train", ok=False,
                detail=f"output_name must be alphanumeric/hyphens/underscores (got {output_name!r})",
            )

        base_model = str(payload.get("base_model", "FLUX")).strip() or "FLUX"
        steps_raw = payload.get("steps", 500)
        try:
            steps = max(1, min(int(steps_raw), 2000))
        except (TypeError, ValueError):
            steps = 500
        lr_raw = payload.get("learning_rate", 0.0001)
        try:
            learning_rate = float(lr_raw)
        except (TypeError, ValueError):
            learning_rate = 0.0001

        # Attempt to enqueue via the imageToVideo trainer if available.
        # Falls back gracefully if the training module is missing.
        try:
            from gateway.image_shim import _IMAGE_BACKEND  # type: ignore
            import sys as _sys
            if not (str(_IMAGE_BACKEND) not in ("", ".") and _IMAGE_BACKEND.is_dir()):
                return ActionReceipt(
                    verb="lora_train", ok=False,
                    detail="image backend not configured (set HIVE_IMAGE_BACKEND_PATH); LoRA training unavailable",
                )
        except Exception:
            return ActionReceipt(
                verb="lora_train", ok=False,
                detail="could not verify the image backend installation",
            )

        import uuid as _uuid
        job_id = _uuid.uuid4().hex[:12]

        # Enqueue via run_in_executor so the sync trainer import and
        # any sync setup don't block the event loop. The trainer's
        # enqueue function is expected to be non-blocking itself —
        # it only registers the job; the actual GPU work runs in a
        # background thread managed by the training pipeline.
        def _sync_enqueue() -> dict:
            try:
                import importlib.util as _util
                trainer_path = _IMAGE_BACKEND / "media" / "lora_train.py"
                if not trainer_path.exists():
                    return {
                        "ok": False,
                        "detail": "lora_train.py not found under the image backend's media/",
                    }
                spec = _util.spec_from_file_location("lora_train_module", trainer_path)
                if spec is None or spec.loader is None:
                    return {"ok": False, "detail": "could not load lora_train module"}
                mod = _util.module_from_spec(spec)
                spec.loader.exec_module(mod)   # type: ignore[attr-defined]
                fn = getattr(mod, "enqueue_training", None) or getattr(
                    mod, "queue_training", None,
                )
                if fn is None:
                    return {
                        "ok": False,
                        "detail": "lora_train module has no enqueue_training or queue_training",
                    }
                fn_result = fn(
                    job_id=job_id,
                    dataset_path=dataset_path,
                    output_name=output_name,
                    base_model=base_model,
                    steps=steps,
                    learning_rate=learning_rate,
                )
                return {"ok": True, "detail": str(fn_result or f"job {job_id} enqueued")}
            except Exception as e:  # noqa: BLE001
                log.exception("lora_train enqueue failed")
                return {"ok": False, "detail": f"{type(e).__name__}: {e}"}

        result = await asyncio.to_thread(_sync_enqueue)

        if not result.get("ok"):
            return ActionReceipt(
                verb="lora_train", ok=False,
                detail=result.get("detail", "enqueue failed"),
                payload={"job_id": job_id},
            )
        return ActionReceipt(
            verb="lora_train", ok=True,
            detail=result.get("detail", f"lora training job {job_id} enqueued"),
            payload={
                "job_id": job_id,
                "output_name": output_name,
                "steps": steps,
            },
        )

    async def _escalate_to_dev(
        self, payload: dict, device_id: str,
    ) -> ActionReceipt:
        """Hive-flagged bug / feature ask routed to the dev (Claude Code).

        Writes a markdown note under `vault/ops/escalations/<ts>.md`
        with the summary, the failure context, and what the user said.
        Claude Code monitors `ops/escalations/` and reads new notes
        on session start.

        Payload shape:
          {"summary": "<one-line>",         # required
           "context": "<longer explanation>",  # required
           "user_msg": "<original user msg>",  # optional, helpful
           "severity": "low" | "medium" | "high"}
        """
        summary = str(payload.get("summary", "")).strip()
        context = str(payload.get("context", "")).strip()
        if not summary or len(context) < 20:
            return ActionReceipt(
                verb="escalate_to_dev", ok=False,
                detail="escalate_to_dev: needs summary + context (≥20 chars)",
            )
        severity = payload.get("severity", "medium")
        if severity not in ("low", "medium", "high"):
            severity = "medium"
        user_msg = str(payload.get("user_msg", ""))[:800]

        if self._vault_client_factory is None:
            return ActionReceipt(verb="escalate_to_dev", ok=False,
                                 detail="vault client not configured")
        # Rate-limit so a runaway loop can't flood the escalation queue.
        if (
            self._rate_limiter is not None
            and device_id
            and not self._rate_limiter.try_acquire(device_id, "vault_actions")
        ):
            return ActionReceipt(
                verb="escalate_to_dev", ok=False,
                detail="rate limit on dev escalations; try again shortly",
            )
        import datetime as _dt
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        title = f"escalation {ts.replace(':', '')[:15]} — {summary[:60]}"
        body = (
            f"**Severity:** {severity}\n"
            f"**Reported at:** {ts}\n"
            f"**Device:** `{device_id}`\n\n"
            f"## Summary\n{summary}\n\n"
            f"## Context\n{context}\n\n"
            f"## User message (verbatim)\n{user_msg or '(none)'}\n"
        )
        client = self._vault_client_factory()
        try:
            resp = await client.learn(
                author="hive",
                category="ops",
                title=title,
                body=body,
                # Audience: claude-code only — this is a dev-channel
                # message, not part of the user's vault context.
                audience=["claude-code"],
                tags=["escalation", severity],
                extra={"escalation_ts": ts, "device_id": device_id},
            )
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(verb="escalate_to_dev", ok=False,
                                 detail=str(e))
        if resp and resp.get("ok"):
            log.info(
                "escalation routed to dev: %s (severity=%s)",
                summary[:80], severity,
            )
            return ActionReceipt(
                verb="escalate_to_dev", ok=True,
                detail=f"flagged to dev at {resp.get('path', '?')}",
                payload={"path": resp.get("path", ""), "severity": severity},
            )
        return ActionReceipt(verb="escalate_to_dev", ok=False,
                             detail="vault_writer rejected the escalation")

    async def _ntfy_push(self, payload: dict) -> ActionReceipt:
        if self._ntfy is None or not getattr(self._ntfy, "enabled", False):
            return ActionReceipt(verb="ntfy_push", ok=False,
                                 detail="ntfy not enabled")
        title = str(payload.get("title", "ai-team"))[:200]
        message = str(payload.get("message", ""))[:1000]
        if not message:
            return ActionReceipt(verb="ntfy_push", ok=False,
                                 detail="missing message")
        try:
            await self._ntfy.publish(
                topic=str(payload.get("topic", "ai-team")),
                title=title, message=message,
                tags=list(payload.get("tags") or []),
            )
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(verb="ntfy_push", ok=False,
                                 detail=str(e))
        return ActionReceipt(verb="ntfy_push", ok=True,
                             detail=f"pushed: {title}")

    async def _create_skill(self, payload: dict) -> ActionReceipt:
        if self._skill_registry is None:
            return ActionReceipt(verb="create_skill", ok=False,
                                 detail="skill registry not configured")
        name = str(payload.get("name", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not name or len(body) < 100:
            return ActionReceipt(
                verb="create_skill", ok=False,
                detail="rubric: need name and body ≥100 chars",
            )
        # Optional Critic re-check (the planner should have already
        # marked this risky, but belt + suspenders).
        if self._critic is not None:
            from gateway.helpers.base import HelperTask
            task = HelperTask(
                role="critic",
                goal="review proposed skill creation",
                inputs={
                    "verb": "create_skill",
                    "payload": payload,
                    "user_msg": payload.get("user_msg", ""),
                    "rationale": payload.get("rationale", ""),
                },
            )
            try:
                review = await asyncio.wait_for(
                    self._critic.invoke(task), timeout=20,
                )
                if review.output.get("block"):
                    return ActionReceipt(
                        verb="create_skill", ok=False,
                        detail=f"critic blocked: {review.output.get('reason','')}",
                    )
            except asyncio.TimeoutError:
                pass    # fail-open on critic timeout
            except Exception as e:  # noqa: BLE001
                log.warning("critic review on create_skill failed: %s", e)

        try:
            skill = self._skill_registry.write_skill(
                name=name, body_with_frontmatter=body,
            )
        except FileExistsError:
            return ActionReceipt(verb="create_skill", ok=False,
                                 detail="skill name already exists")
        except Exception as e:  # noqa: BLE001
            return ActionReceipt(verb="create_skill", ok=False,
                                 detail=str(e))
        return ActionReceipt(
            verb="create_skill", ok=True,
            detail=f"wrote {skill.path.name}",
            payload={"name": skill.name, "path": str(skill.path)},
        )

    def _image_build_update(
        self, payload: dict, device_id: str,
    ) -> ActionReceipt:
        if self._image_build_store is None:
            return ActionReceipt(verb="image_build_update", ok=False,
                                 detail="image build store not configured")
        if not device_id:
            return ActionReceipt(verb="image_build_update", ok=False,
                                 detail="missing device_id")
        changed = self._image_build_store.update(device_id, payload)
        if not changed:
            return ActionReceipt(verb="image_build_update", ok=True,
                                 detail="no changes")
        return ActionReceipt(
            verb="image_build_update", ok=True,
            detail=f"updated slots: {changed}",
            payload={"changed": changed},
        )


# ---------------------------------------------------------------- helpers


def _aspect_dims(aspect: str) -> tuple[int, int]:
    # _ASPECT_DIMS is imported from image_catalog so synthesizer-emitted
    # aspects like "wallpaper" don't silently fall back to 1024×1024 just
    # because the executor's table forgot to mirror the catalog.
    return _ASPECT_DIMS.get(aspect, (1024, 1024))


def _read_audience(path) -> list[str] | None:
    """Pull the `audience` field from a vault note's YAML frontmatter.

    Returns the list of audience names, or None if the note can't be
    read or has malformed frontmatter (caller should fail-closed in
    that case — never delete notes whose audience we can't verify).
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        from vault_writer.util import parse_frontmatter, coerce_audience
    except ImportError:
        return None
    try:
        fm, _body = parse_frontmatter(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(fm, dict):
        return None
    return coerce_audience(fm.get("audience"))
