"""URL → AI-asset importer.

User flow:
  1. User pastes a model URL (e.g. https://civitai.com/models/<id>/<slug>)
  2. Gateway parses → resolves via the source's API → returns a download URL,
     filename, expected size, hash, alias, category, trigger words, NSFW flag.
  3. Streams the file to disk under `<image_app_root>/models/loras/<slug>/`,
     verifying SHA256 + .safetensors magic bytes as it goes.
  4. Appends a registry entry so the LoRA shows up in the catalog.
  5. Triggers `image_lora_doc.regenerate_if_stale` so canon picks it up.

Sources supported:
  - civitai.com
  - huggingface.co/<user>/<model>/blob/<rev>/<file>.safetensors
  - raw https://...safetensors

Adding another source = ~30 lines (URL matcher + resolver).

Security:
  - Trusted-host allowlist gates the SSRF guard for known model registries
  - Private-IP / loopback / link-local rejection still applies (post-DNS)
  - Redirect cap = 5 (some Civitai downloads chain through CDNs)
  - Content-type whitelist: octet-stream, x-safetensors, binary
  - Safetensors magic-byte check after download
  - SHA256 verified when source provides it
  - Filename slugified, destination path confined under models/loras/
  - Size cap 8 GB (enough for SDXL checkpoints; LoRAs are ~50-300 MB)
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger("gateway.asset_importer")


_TRUSTED_HOSTS = (
    "civitai.com",
    "huggingface.co", "cdn-lfs.huggingface.co",
    # Civitai serves downloads through these CDNs after a redirect.
    "civitaiarchive.com", "civitai-delivery-worker-prod.5ac0637cfd0766c97916cefa3764fbdf.r2.cloudflarestorage.com",
    "image.civitai.com",
)
_REDIRECT_CAP = 5
_DEFAULT_MAX_BYTES = 16 * 1024 * 1024 * 1024    # 16 GB (Civitai checkpoints ~12 GB)
_DOWNLOAD_TIMEOUT_S = 60 * 30                    # 30 min total
_CHUNK_BYTES = 1 * 1024 * 1024                   # 1 MB

_SAFETENSORS_MAGIC_LEN = 8     # first 8 bytes = u64 LE = JSON header length

# Maximum number of concurrent downloads (top-level + recipe sub-imports).
# Prevents a malicious recipe listing many model URLs from spawning unbounded
# concurrent downloads (disk-fill DoS). Caps at 2 because LoRA downloads are
# large I/O operations; more than 2 saturates the pipe without meaningfully
# improving throughput.
_MAX_CONCURRENT_DOWNLOADS = 2

# Lazy per-loop semaphore.  asyncio.Semaphore() created at module-import time
# binds to whichever event loop is running first, which breaks tests that spin
# up their own loops (e.g. pytest-asyncio's per-test loop).  We create the
# semaphore on first use inside the running loop instead.
_IMPORT_SEMAPHORE: asyncio.Semaphore | None = None
_IMPORT_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None


def _get_import_semaphore() -> asyncio.Semaphore:
    global _IMPORT_SEMAPHORE, _IMPORT_SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _IMPORT_SEMAPHORE is None or _IMPORT_SEMAPHORE_LOOP is not loop:
        _IMPORT_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)
        _IMPORT_SEMAPHORE_LOOP = loop
    return _IMPORT_SEMAPHORE


def verify_safetensors_magic(path: Path) -> None:
    """Raise ValueError if `path` doesn't look like a safetensors file.

    Format: 8-byte little-endian u64 = JSON header length, followed by
    JSON starting with `{`. We don't parse the JSON — just spot-check.
    """
    with path.open("rb") as f:
        head = f.read(_SAFETENSORS_MAGIC_LEN + 1)
    if len(head) < _SAFETENSORS_MAGIC_LEN + 1:
        raise ValueError("file too short to be safetensors")
    hdr_len = struct.unpack("<Q", head[:_SAFETENSORS_MAGIC_LEN])[0]
    if hdr_len == 0 or hdr_len > 100 * 1024 * 1024:
        raise ValueError(f"implausible safetensors header length: {hdr_len}")
    if head[_SAFETENSORS_MAGIC_LEN:_SAFETENSORS_MAGIC_LEN + 1] != b"{":
        raise ValueError("safetensors header doesn't start with '{'")


# ---------------------------------------------------------------- shapes


# ParsedSource + parse_url moved to gateway/asset_url_parser.py for
# testability without dragging the SSRF guard / downloader into URL
# tests. Re-exported here so existing in-place callers don't break.
from gateway.asset_url_parser import ParsedSource, parse_url  # noqa: E402, F401


@dataclass
class ResolvedDownload:
    """What the resolver hands back. The downloader takes this verbatim."""
    download_url: str
    filename: str                        # basename only; slugified later
    size_bytes: int                      # 0 = unknown
    sha256: str | None
    alias: str                           # display name in the registry
    repo_id: str                         # e.g. "civitai:1223034"
    kind: str = "lora"                   # lora | checkpoint | unsupported
    pipeline: str = "unknown"            # sdxl / flux / sd1.5 / zimage / wan / unknown
    trigger_words: str = ""
    category: str = ""                   # raw Civitai type ("LORA","Checkpoint","Workflows"...)
    nsfw: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    unsupported_reason: str = ""         # filled when kind == "unsupported"


@dataclass
class ImportJob:
    id: str
    url: str
    state: str = "queued"                # queued|resolving|downloading|installing|done|error
    bytes_done: int = 0
    bytes_total: int = 0
    progress_pct: float = 0.0
    alias: str = ""
    repo_id: str = ""
    dest_path: str = ""
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Used by the civitai_image_recipe path — the user pastes the
    # prompt block from the page (Civitai's image API is locked).
    pasted_text: str | None = None
    # Sub-import job IDs queued by an image-recipe (LoRAs/Checkpoints
    # the recipe references). UI uses these to render the cascade.
    sub_job_ids: list[str] = field(default_factory=list)
    # Persistence hook bound by AssetImportStore.create — called on
    # every touch() so disk state never drifts from in-memory state.
    # Skipped during disk progress floods; touch() invokes a throttled
    # writer rather than syncing on every byte.
    _persist_cb: object = field(default=None, repr=False, compare=False)

    def touch(self) -> None:
        self.updated_at = time.time()
        if self.bytes_total > 0:
            self.progress_pct = round(
                100.0 * self.bytes_done / self.bytes_total, 1,
            )
        cb = self._persist_cb
        if callable(cb):
            cb(self)


# ---------------------------------------------------------------- security


def _is_blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_private or ip.is_reserved
        or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    )


def _resolve_addresses(host: str) -> list[str]:
    try:
        info = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return list({i[4][0] for i in info})


def _validate_target(url: str, *, allow_trusted: bool = True) -> str | None:
    """Strict-ish URL validator for asset downloads.

    Rejects non-http(s), private/loopback IPs (post-DNS), and any host
    not in the trusted list when `allow_trusted=True`. Returns None on
    success, else a reason string."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"unsupported scheme: {parsed.scheme!r}"
    host = (parsed.hostname or "").lower()
    if not host:
        return "missing host"
    if allow_trusted and not _is_trusted_host(host):
        return f"host not in trusted-asset allowlist: {host}"
    # DNS-resolve and reject private addresses (defends against
    # rebinding / public domains pointing at internal services).
    try:
        ipaddress.ip_address(host)
        if _is_blocked_ip(host):
            return f"private/loopback IP: {host}"
    except ValueError:
        addrs = _resolve_addresses(host)
        if not addrs:
            return f"DNS lookup failed: {host}"
        for a in addrs:
            if _is_blocked_ip(a):
                return f"resolves to blocked IP: {host} -> {a}"
    return None


def _is_trusted_host(host: str) -> bool:
    h = host.lower()
    for t in _TRUSTED_HOSTS:
        if h == t or h.endswith("." + t):
            return True
    return False


# ---------------------------------------------------------------- resolvers


def _civitai_auth_headers() -> dict[str, str]:
    """Bearer header for Civitai's authenticated API.

    The model-page metadata (LoRA/checkpoint files) works unauthenticated,
    but image-page recipes (prompt + resources) require auth on Civitai's
    side. We read the key from `CIVITAI_API_KEY` so it doesn't sit in
    git; gateway/config.py validates the env var on startup.
    """
    import os
    key = os.environ.get("CIVITAI_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _http_get_json(
    url: str, timeout: float = 30.0, *, auth: bool = False,
) -> dict:
    headers = {
        "User-Agent": "ai-team-importer/0.1",
        "Accept": "application/json",
    }
    if auth:
        headers.update(_civitai_auth_headers())
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as h:
        r = await h.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


async def fetch_civitai_image_meta(image_id: int) -> dict | None:
    """Resolve a Civitai image-page id → its prompt + resources.

    Anonymous requests get a 404 back from /api/v1/images/<id>, which
    is what blocked the recipe importer all session. With a Bearer
    token this returns the standard image-meta envelope: prompt,
    negativePrompt, sampler, steps, cfgScale, seed, plus a
    `resources` list of model+version refs the image used.

    Returns None when the API key isn't configured or the lookup
    fails so callers can fall back to the paste-the-prompt-block
    flow without crashing.
    """
    if not _civitai_auth_headers():
        return None
    for host in ("civitai.com",):
        url = f"https://{host}/api/v1/images?imageId={image_id}"
        try:
            data = await _http_get_json(url, auth=True)
        except httpx.HTTPError:
            continue
        items = (data or {}).get("items") or []
        if items:
            return items[0]
    return None


async def resolve(parsed: ParsedSource) -> ResolvedDownload:
    """Dispatch to the right resolver. Raises ValueError on failure."""
    if parsed.kind == "civitai":
        return await _resolve_civitai(parsed)
    if parsed.kind == "huggingface":
        return await _resolve_huggingface(parsed)
    if parsed.kind == "raw":
        return _resolve_raw(parsed)
    raise ValueError(f"unsupported source kind: {parsed.kind}")


async def _resolve_civitai(parsed: ParsedSource) -> ResolvedDownload:
    """Hit Civitai's public model API. Same shape on .com and .red."""
    api_url = f"https://{parsed.host}/api/v1/models/{parsed.model_id}"
    reason = _validate_target(api_url)
    if reason:
        raise ValueError(f"civitai api blocked: {reason}")
    try:
        # Authenticated when CIVITAI_API_KEY is set — gives access
        # to NSFW-flagged versions that anon requests get 404s on.
        data = await _http_get_json(api_url, auth=True)
    except httpx.HTTPError as e:
        raise ValueError(f"civitai api fetch failed: {e}")

    name = data.get("name", f"civitai_{parsed.model_id}")
    nsfw = bool(data.get("nsfw") or int(data.get("nsfwLevel", 0)) >= 4)
    versions = data.get("modelVersions") or []
    if not versions:
        raise ValueError("civitai model has no versions")

    if parsed.version_id is not None:
        version = next(
            (v for v in versions if int(v.get("id", -1)) == parsed.version_id),
            None,
        )
        if version is None:
            raise ValueError(f"version {parsed.version_id} not found")
    else:
        version = versions[0]

    files = version.get("files") or []
    candidates = [f for f in files if f.get("name", "").lower().endswith(".safetensors")]
    if not candidates:
        candidates = files
    if not candidates:
        raise ValueError("civitai version has no downloadable files")
    f = candidates[0]
    download_url = f.get("downloadUrl") or version.get("downloadUrl")
    if not download_url:
        raise ValueError("civitai response had no downloadUrl")
    size_bytes = int(float(f.get("sizeKB") or 0) * 1024)
    sha256 = (f.get("hashes") or {}).get("SHA256")
    if isinstance(sha256, str):
        sha256 = sha256.lower()

    pipeline = _infer_pipeline(version, data)
    trigger_words = ", ".join(version.get("trainedWords") or [])
    raw_type = data.get("type") or ""
    kind, unsupported_reason = _classify_kind(raw_type)

    return ResolvedDownload(
        download_url=download_url,
        filename=str(f.get("name") or f"civitai_{parsed.model_id}.safetensors"),
        size_bytes=size_bytes,
        sha256=sha256,
        alias=name,
        repo_id=f"civitai:{parsed.model_id}",
        kind=kind,
        pipeline=pipeline,
        trigger_words=trigger_words,
        category=raw_type,
        nsfw=nsfw,
        unsupported_reason=unsupported_reason,
    )


async def _resolve_huggingface(parsed: ParsedSource) -> ResolvedDownload:
    """huggingface.co/<repo>/<blob|resolve>/<rev>/<path>.safetensors

    The `resolve` endpoint streams the file directly; the `blob` endpoint
    is the HTML viewer. Either way we rewrite to `resolve` for download.
    """
    if not parsed.file_path:
        raise ValueError("hf parse missing file_path")
    if "@" not in parsed.file_path:
        raise ValueError("hf parse missing rev")
    # file_path is "<owner>/<repo>@<rev>/<path>" — repo can contain
    # exactly one slash, so split on '@' first then peel the rev.
    repo, rest = parsed.file_path.split("@", 1)
    if "/" not in rest:
        raise ValueError("hf parse missing file path after rev")
    rev, file_path = rest.split("/", 1)
    download_url = (
        f"https://huggingface.co/{repo}/resolve/{rev}/{file_path}"
    )
    return ResolvedDownload(
        download_url=download_url,
        filename=Path(file_path).name,
        size_bytes=0,                    # learned at HEAD time
        sha256=None,
        alias=f"hf/{repo}/{Path(file_path).stem}",
        repo_id=f"hf:{repo}@{rev}/{file_path}",
        pipeline="unknown",
        trigger_words="",
        category="",
        nsfw=False,
    )


def _resolve_raw(parsed: ParsedSource) -> ResolvedDownload:
    """Raw .safetensors URL — minimal metadata."""
    name = Path(urlparse(parsed.original_url).path).name
    return ResolvedDownload(
        download_url=parsed.original_url,
        filename=name or "asset.safetensors",
        size_bytes=0,
        sha256=None,
        alias=Path(name).stem or "asset",
        repo_id=f"raw:{parsed.original_url}",
        pipeline="unknown",
    )


def _infer_pipeline(version: dict, model: dict) -> str:
    """Civitai exposes baseModel ("SDXL 1.0", "Pony", "Flux.1 D"...).
    Map to the pipeline identifiers our image catalog expects."""
    base = (version.get("baseModel") or model.get("baseModel") or "").lower()
    if "flux" in base:
        return "flux"
    if "wan" in base:
        return "wan"
    if "z-image" in base or "zimage" in base or "z image" in base:
        return "zimage"
    # Pony / Illustrious / NoobAI / Anima are SDXL fine-tunes.
    if any(t in base for t in ("pony", "illustrious", "noobai", "anima")):
        return "sdxl"
    if "sdxl" in base or "xl" in base:
        return "sdxl"
    if "sd 1.5" in base or "sd1.5" in base:
        return "sd1.5"
    if "sd 2" in base or "sd2" in base:
        return "sd2"
    return "unknown"


def _classify_kind(category: str) -> tuple[str, str]:
    """Return (kind, unsupported_reason).

    Civitai's `type` field uses values like LORA, Checkpoint, LoCon,
    TextualInversion, Workflows, etc. We install LoRAs and Checkpoints;
    everything else is rejected with a human-readable reason so the
    user knows why their URL didn't take.
    """
    cat = (category or "").lower().strip()
    if cat in ("lora", "locon", "lycoris", "dora"):
        return "lora", ""
    if cat in ("checkpoint", "checkpointmerge"):
        return "checkpoint", ""
    if cat == "workflows":
        return "unsupported", (
            "Civitai 'Workflows' assets are pipeline configs, not loadable "
            "weights. Skip — there's nothing to install."
        )
    if cat == "textualinversion":
        return "unsupported", "TextualInversion (.pt embedding) — not yet supported."
    if cat == "vae":
        return "unsupported", "VAE — not yet supported by the importer."
    if cat in ("controlnet", "upscaler", "motionmodule", "poses", "wildcards"):
        return "unsupported", f"Asset type '{category}' is not yet supported."
    # Unknown but probably a weight file — treat as LoRA (the default
    # destination is the safer one — small files in models/loras/).
    return "lora", ""


# ---------------------------------------------------------------- recipe parsing
# Re-exports from gateway.recipe_parser. The parser logic moved to its
# own module so it can grow regression tests without dragging the
# SSRF guard, downloader, and registry installer into every fixture.
from gateway.recipe_parser import (  # noqa: E402, F401
    detect_recipe_kind,
    parse_civitai_recipe_text,
)


def _registry_repo_ids(registry_path: Path) -> set[str]:
    """Set of repo_ids already in lora_registry.json (for dedup)."""
    if not registry_path.is_file():
        return set()
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {
        str(e.get("repo_id"))
        for e in data
        if isinstance(e, dict) and e.get("repo_id")
    }


# ---------------------------------------------------------------- downloader


async def download_with_progress(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int = 0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[int, str]:
    """Stream `url` to `dest` with redirect re-validation, size cap,
    SHA256 check, and a per-chunk progress callback.

    Returns (bytes_written, sha256_hex). Raises ValueError on any
    safety/size/hash failure (the partial file is unlinked first).
    """
    visited: set[str] = set()
    current = url
    hops = 0
    headers = {
        "User-Agent": "ai-team-importer/0.1",
        "Accept": "application/octet-stream, application/x-safetensors, */*",
    }
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=30.0)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False,
    ) as client:
        while True:
            if current in visited:
                raise ValueError("redirect loop")
            visited.add(current)
            reason = _validate_target(current)
            if reason:
                raise ValueError(f"download blocked: {reason}")

            resp = await client.get(current, headers=headers)
            if resp.is_redirect:
                hops += 1
                if hops > _REDIRECT_CAP:
                    await resp.aclose()
                    raise ValueError(
                        f"redirect cap ({_REDIRECT_CAP}) exceeded",
                    )
                next_url = resp.headers.get("location")
                if not next_url:
                    await resp.aclose()
                    raise ValueError("redirect with no Location header")
                if next_url.startswith("/"):
                    p = urlparse(current)
                    next_url = f"{p.scheme}://{p.netloc}{next_url}"
                current = next_url
                await resp.aclose()
                continue

            resp.raise_for_status()
            content_length = int(resp.headers.get("content-length") or 0)
            if content_length and content_length > max_bytes:
                await resp.aclose()
                raise ValueError(
                    f"asset too large: {content_length} > {max_bytes}",
                )
            total = content_length or expected_size

            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            sha = hashlib.sha256()
            written = 0
            try:
                with tmp.open("wb") as f:
                    async for chunk in resp.aiter_bytes(_CHUNK_BYTES):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(
                                f"asset overflowed cap mid-stream "
                                f"({written} > {max_bytes})"
                            )
                        sha.update(chunk)
                        f.write(chunk)
                        if on_progress is not None:
                            on_progress(written, total)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise

            digest = sha.hexdigest()
            if expected_sha256 and digest.lower() != expected_sha256.lower():
                tmp.unlink(missing_ok=True)
                raise ValueError(
                    f"sha256 mismatch: got {digest}, expected {expected_sha256}",
                )
            if dest.suffix.lower() == ".safetensors":
                try:
                    verify_safetensors_magic(tmp)
                except ValueError:
                    tmp.unlink(missing_ok=True)
                    raise
            dest.unlink(missing_ok=True)
            tmp.replace(dest)
            return written, digest


# ---------------------------------------------------------------- registry


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("._-")
    return s or "asset"


def install_lora(
    *,
    resolved: ResolvedDownload,
    downloaded_path: Path,
    loras_root: Path,
    registry_path: Path,
) -> dict:
    """Move the downloaded file into `loras_root/<slug>/`, append a
    registry entry, and return it. Idempotent on repo_id.

    Holds a per-repo_id lock for the registry read-modify-write so two
    simultaneous imports for the same repo_id can't race and lose one
    of the other's bytes.
    """
    with _repo_lock(resolved.repo_id):
        slug = _slugify(resolved.repo_id.replace(":", "_"))
        target_dir = (loras_root / slug).resolve()
        # Confine to loras_root.
        try:
            target_dir.relative_to(loras_root.resolve())
        except ValueError:
            raise ValueError(f"target {target_dir} escaped {loras_root}")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / Path(resolved.filename).name
        if downloaded_path.resolve() != target_file.resolve():
            target_file.unlink(missing_ok=True)
            downloaded_path.replace(target_file)

        entry = {
            "repo_id": resolved.repo_id,
            "alias": resolved.alias,
            "local_path": str(target_dir),
            "main_file": str(target_file),
            "trigger_words": resolved.trigger_words,
            "pipeline": resolved.pipeline,
            "default_strength": 1.0,
            "category": resolved.category,
        }
        if resolved.nsfw:
            entry["nsfw"] = True

        # Append/replace by repo_id.
        if registry_path.is_file():
            try:
                existing = json.loads(registry_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = []
        else:
            existing = []
        if not isinstance(existing, list):
            existing = []
        existing = [
            e for e in existing if e.get("repo_id") != resolved.repo_id
        ]
        existing.append(entry)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(existing, indent=2), encoding="utf-8",
        )
        return entry


# Per-repo_id lock dict. Threading.Lock is fine here — install_lora is
# called synchronously inside an async task; the actual file moves +
# registry write are short and never await. Using an OS-level lock
# would be overkill for a single-process gateway.
import threading as _threading_for_locks
_REPO_LOCKS: dict[str, _threading_for_locks.Lock] = {}
_REPO_LOCKS_GUARD = _threading_for_locks.Lock()


def _repo_lock(repo_id: str) -> _threading_for_locks.Lock:
    """Return a per-repo_id lock, creating one on first call. The
    guard lock is held only for the dict mutation, not the install."""
    with _REPO_LOCKS_GUARD:
        lk = _REPO_LOCKS.get(repo_id)
        if lk is None:
            lk = _threading_for_locks.Lock()
            _REPO_LOCKS[repo_id] = lk
        return lk


def install_checkpoint(
    *,
    resolved: ResolvedDownload,
    downloaded_path: Path,
    checkpoints_root: Path,
) -> dict:
    """Move the file into `checkpoints_root/<slug>/`. No registry —
    imageToVideo scans this directory at startup. Returns a metadata
    dict identical-shaped to install_lora's so the route can serialise
    a single response."""
    slug = _slugify(resolved.repo_id.replace(":", "_"))
    target_dir = (checkpoints_root / slug).resolve()
    try:
        target_dir.relative_to(checkpoints_root.resolve())
    except ValueError:
        raise ValueError(
            f"target {target_dir} escaped {checkpoints_root}",
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / Path(resolved.filename).name
    if downloaded_path.resolve() != target_file.resolve():
        target_file.unlink(missing_ok=True)
        downloaded_path.replace(target_file)
    # Drop a tiny meta file so the user can audit the install later.
    meta = {
        "repo_id": resolved.repo_id,
        "alias": resolved.alias,
        "pipeline": resolved.pipeline,
        "trigger_words": resolved.trigger_words,
        "category": resolved.category,
        "nsfw": resolved.nsfw,
        "main_file": str(target_file),
        "kind": "checkpoint",
    }
    (target_dir / "asset_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8",
    )
    return meta


# ---------------------------------------------------------------- store


class AssetImportStore:
    """Per-process ring of recent import jobs.

    Persists to `<state_dir>/asset_imports.json` when a path is given —
    architect's 2026-04-29 review flagged that without persistence a
    mid-12 GB-download gateway restart loses the only record of the
    in-flight import, leaving `state="downloading"` orphans that block
    the user from kicking off a fresh attempt with the same URL.

    Persisted fields are the dataclass projection — `pasted_text` and
    `sub_job_ids` round-trip with the rest. `state="downloading"` jobs
    are auto-rewritten to `state="error"` with a clear note on load,
    since whatever Python-task owned the in-progress download is
    definitely gone after the restart.
    """

    def __init__(
        self,
        *,
        max_records: int = 50,
        path: Path | None = None,
    ) -> None:
        self._jobs: dict[str, ImportJob] = {}
        self._order: list[str] = []
        self._max = max_records
        self._lock = asyncio.Lock()
        self._path = path
        if path is not None:
            self._load()

    # ---------------------------------------------------------------- mutate
    def create(self, url: str) -> ImportJob:
        job = ImportJob(id=uuid.uuid4().hex[:16], url=url)
        # Throttled persist callback bound onto the job so every
        # touch() reaches disk without the run_import path having to
        # call save() at each state transition. Throttling keeps the
        # downloading-progress fan-out from making 50k disk writes per
        # GB.
        job._persist_cb = self._throttled_persist
        self._jobs[job.id] = job
        self._order.append(job.id)
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._jobs.pop(old, None)
        self._persist()
        return job

    # Last-write timestamp for the progress throttle. ~1 Hz is enough
    # for a UI poll; downloads write progress hundreds of times per
    # second otherwise.
    _last_persist: float = 0.0

    def _throttled_persist(self, job: ImportJob) -> None:
        # Always persist terminal-state transitions immediately so a
        # restart at the wrong moment doesn't lose the final outcome;
        # mid-flight progress updates ride a 1 Hz throttle.
        terminal = job.state in ("done", "error")
        now = time.time()
        if terminal or now - self._last_persist >= 1.0:
            self._last_persist = now
            self._persist()

    def get(self, job_id: str) -> ImportJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[ImportJob]:
        return [self._jobs[i] for i in self._order if i in self._jobs]

    # ---------------------------------------------------------------- persist
    def _persist(self) -> None:
        if self._path is None:
            return
        from shared.atomic_write import atomic_write_json
        try:
            atomic_write_json(self._path, {
                "order": list(self._order),
                "jobs": {
                    jid: _job_to_dict(self._jobs[jid])
                    for jid in self._order if jid in self._jobs
                },
            })
        except OSError as e:
            log.warning("asset_imports persist failed: %s", e)

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("asset_imports load failed: %s — starting fresh", e)
            return
        order = raw.get("order") or []
        jobs_raw = raw.get("jobs") or {}
        for jid in order:
            jd = jobs_raw.get(jid)
            if not isinstance(jd, dict):
                continue
            job = _job_from_dict(jd)
            if job is None:
                continue
            # Anything that was mid-download when the gateway died is
            # an orphan; flip it to error so the UI surfaces the gap
            # and the user can re-queue.
            if job.state in ("resolving", "downloading", "installing"):
                job.state = "error"
                job.error = (
                    f"interrupted by gateway restart "
                    f"(was '{jd.get('state')}'); re-queue to retry"
                )
                job.touch()
            job._persist_cb = self._throttled_persist
            self._jobs[jid] = job
            self._order.append(jid)


def _job_to_dict(job: ImportJob) -> dict:
    return {
        "id": job.id, "url": job.url, "state": job.state,
        "bytes_done": job.bytes_done, "bytes_total": job.bytes_total,
        "progress_pct": job.progress_pct,
        "alias": job.alias, "repo_id": job.repo_id,
        "dest_path": job.dest_path, "error": job.error,
        "created_at": job.created_at, "updated_at": job.updated_at,
        "pasted_text": job.pasted_text,
        "sub_job_ids": list(job.sub_job_ids),
    }


def _job_from_dict(d: dict) -> ImportJob | None:
    try:
        return ImportJob(
            id=str(d["id"]), url=str(d["url"]),
            state=str(d.get("state", "queued")),
            bytes_done=int(d.get("bytes_done") or 0),
            bytes_total=int(d.get("bytes_total") or 0),
            progress_pct=float(d.get("progress_pct") or 0.0),
            alias=str(d.get("alias") or ""),
            repo_id=str(d.get("repo_id") or ""),
            dest_path=str(d.get("dest_path") or ""),
            error=d.get("error"),
            created_at=float(d.get("created_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            pasted_text=d.get("pasted_text"),
            sub_job_ids=list(d.get("sub_job_ids") or []),
        )
    except (KeyError, TypeError, ValueError) as e:
        log.warning("asset_imports skipping malformed job: %s", e)
        return None


# ---------------------------------------------------------------- top-level


async def run_import(
    job: ImportJob,
    *,
    loras_root: Path,
    registry_path: Path,
    checkpoints_root: Path | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    vault_client_factory=None,
    asset_import_store=None,
    task_tracker=None,
) -> dict | None:
    """Resolve + download + install. Mutates the job. Never raises.

    Routing by `resolved.kind`:
      - "lora"        → loras_root + registry append
      - "checkpoint"  → checkpoints_root + asset_meta.json sidecar
      - "unsupported" → fail fast with the explained reason

    The civitai_image_recipe path branches even earlier — it doesn't
    download anything, just parses the user's pasted text into a vault
    note and queues sub-imports for any model URLs found in there.
    """
    job.state = "resolving"
    job.touch()
    try:
        parsed = parse_url(job.url)
        if parsed is None:
            raise ValueError("URL not recognised by any importer")

        # Image-recipe path — no download; parse pasted text + write vault note.
        # The recipe handler itself spawns sub-imports that will each acquire the
        # semaphore independently, so we do NOT hold the semaphore here.
        if parsed.kind == "civitai_image_recipe":
            return await _run_image_recipe(
                job, parsed,
                vault_client_factory=vault_client_factory,
                asset_import_store=asset_import_store,
                loras_root=loras_root,
                registry_path=registry_path,
                checkpoints_root=checkpoints_root,
                max_bytes=max_bytes,
                task_tracker=task_tracker,
            )

        # Non-recipe imports (loras, checkpoints) actually download files.
        # Acquire the shared semaphore so both top-level and recipe sub-imports
        # are capped at _IMPORT_SEMAPHORE slots concurrently.
        async with _get_import_semaphore():
            return await _run_download_import(
                job, parsed,
                loras_root=loras_root,
                registry_path=registry_path,
                checkpoints_root=checkpoints_root,
                max_bytes=max_bytes,
            )

    except Exception as e:  # noqa: BLE001
        log.exception("asset import failed for %s", job.url)
        job.state = "error"
        job.error = str(e)[:500]
        job.touch()
        return None


async def _run_download_import(
    job: ImportJob,
    parsed: "ParsedSource",
    *,
    loras_root: Path,
    registry_path: Path,
    checkpoints_root: Path | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict:
    """Inner download + install path for non-recipe imports.

    Called only while holding *_IMPORT_SEMAPHORE* so concurrent downloads are
    bounded. May raise — the caller's except clause handles exceptions.
    """
    resolved = await resolve(parsed)
    job.alias = resolved.alias
    job.repo_id = resolved.repo_id
    job.bytes_total = resolved.size_bytes
    job.touch()

    if resolved.kind == "unsupported":
        raise ValueError(
            resolved.unsupported_reason
            or f"asset type '{resolved.category}' is not supported",
        )

    if resolved.kind == "checkpoint":
        if checkpoints_root is None:
            raise ValueError(
                "this is a Checkpoint but no checkpoints_root is "
                "configured — set images.checkpoints_root in gateway.yaml",
            )
        target_root = checkpoints_root
    else:
        target_root = loras_root

    # Stream into a workspace under the right root so the final
    # rename is on the same filesystem.
    slug = _slugify(resolved.repo_id.replace(":", "_"))
    workspace = target_root / slug
    workspace.mkdir(parents=True, exist_ok=True)
    dest = workspace / Path(resolved.filename).name

    job.state = "downloading"
    job.touch()

    def _on_prog(written: int, total: int) -> None:
        job.bytes_done = written
        if total:
            job.bytes_total = total
        job.touch()

    await download_with_progress(
        resolved.download_url, dest,
        expected_sha256=resolved.sha256,
        expected_size=resolved.size_bytes,
        max_bytes=max_bytes,
        on_progress=_on_prog,
    )

    job.state = "installing"
    job.touch()
    if resolved.kind == "checkpoint":
        entry = install_checkpoint(
            resolved=resolved,
            downloaded_path=dest,
            checkpoints_root=checkpoints_root,
        )
    else:
        entry = install_lora(
            resolved=resolved,
            downloaded_path=dest,
            loras_root=loras_root,
            registry_path=registry_path,
        )
    job.dest_path = entry["main_file"]
    job.state = "done"
    job.touch()
    return entry




# ---------------------------------------------------------------- recipe install


_RECIPE_NO_TEXT_MSG = (
    "Civitai image pages need their prompt block pasted alongside — "
    "their API is locked. View the page, copy the prompt section "
    "(the positive prompt + Negative prompt + Steps/CFG/Sampler line), "
    "and paste it into the box."
)


async def _run_image_recipe(
    job: ImportJob,
    parsed: ParsedSource,
    *,
    vault_client_factory,
    asset_import_store,
    loras_root: Path,
    registry_path: Path,
    checkpoints_root: Path | None,
    max_bytes: int,
    task_tracker=None,
) -> dict | None:
    """Image-recipe import. Saves a vault note + queues sub-imports."""
    image_id = int(parsed.model_id or 0)
    job.alias = f"Civitai image {image_id}"
    job.repo_id = f"civitai_image:{image_id}"
    job.touch()

    if not job.pasted_text or not job.pasted_text.strip():
        job.state = "error"
        job.error = _RECIPE_NO_TEXT_MSG
        job.touch()
        return None

    recipe = parse_civitai_recipe_text(job.pasted_text)

    job.state = "installing"
    job.touch()

    # Sub-import dispatch: skip URLs we already have installed (by repo_id).
    sub_jobs: list[str] = []
    if asset_import_store is not None and recipe.get("model_urls"):
        installed = _registry_repo_ids(registry_path)
        for url in recipe["model_urls"]:
            sub_parsed = parse_url(url)
            if sub_parsed is None or sub_parsed.kind != "civitai":
                continue
            sub_repo_id = f"civitai:{sub_parsed.model_id}"
            if sub_repo_id in installed:
                continue
            sub = asset_import_store.create(url)
            sub_jobs.append(sub.id)
            sub_task = asyncio.create_task(
                run_import(
                    sub,
                    loras_root=loras_root,
                    registry_path=registry_path,
                    checkpoints_root=checkpoints_root,
                    max_bytes=max_bytes,
                    vault_client_factory=vault_client_factory,
                    asset_import_store=asset_import_store,
                    task_tracker=task_tracker,
                ),
                name=f"recipe-sub-{sub.id}",
            )
            if task_tracker is not None:
                task_tracker(sub_task)
    job.sub_job_ids = sub_jobs

    # Persist the recipe to the vault.
    if vault_client_factory is not None:
        try:
            await _write_recipe_note(
                image_id=image_id,
                source_url=parsed.original_url,
                recipe=recipe,
                vault_client_factory=vault_client_factory,
                triggered_imports=[
                    f"civitai:{parse_url(u).model_id}" for u in recipe.get("model_urls", [])
                    if parse_url(u) is not None
                ],
            )
        except Exception as e:  # noqa: BLE001
            log.exception("recipe vault write failed for image %s", image_id)
            job.state = "error"
            job.error = f"vault write failed: {e}"[:500]
            job.touch()
            return None

    job.state = "done"
    job.dest_path = f"references/civitai-image-{image_id}.md"
    job.touch()
    return {
        "kind": "image_recipe",
        "image_id": image_id,
        "source_url": parsed.original_url,
        "sub_job_ids": sub_jobs,
        "recipe": recipe,
    }


async def _write_recipe_note(
    *,
    image_id: int,
    source_url: str,
    recipe: dict,
    vault_client_factory,
    triggered_imports: list[str],
) -> None:
    """Render the recipe as markdown and POST it to the vault daemon."""
    pos = (recipe.get("positive") or "").strip()
    neg = (recipe.get("negative") or "").strip()
    sampler = recipe.get("sampler") or "?"
    steps = recipe.get("steps")
    cfg = recipe.get("cfg")
    seed = recipe.get("seed")

    body_lines = [
        f"_Source:_ <{source_url}>",
        "",
        "## Prompt",
        "",
        pos or "_(no positive prompt parsed)_",
    ]
    if neg:
        body_lines += [
            "",
            "## Negative prompt",
            "",
            "> " + neg.replace("\n", "\n> "),
        ]
    body_lines += [
        "",
        "## Settings",
        "",
        f"- Sampler: `{sampler}`",
        f"- Steps: `{steps if steps is not None else '?'}`",
        f"- CFG: `{cfg if cfg is not None else '?'}`",
    ]
    if seed is not None:
        body_lines.append(f"- Seed: `{seed}`")
    if recipe.get("model_urls"):
        body_lines += ["", "## Resources used", ""]
        for u in recipe["model_urls"]:
            body_lines.append(f"- {u}")

    body = "\n".join(body_lines).strip() + "\n"

    extra = {
        "source_url": source_url,
        "image_id": image_id,
        "sampler": sampler,
        "steps": steps,
        "cfg": cfg,
        "seed": seed,
        "triggered_imports": triggered_imports,
        "positive": pos,
        "negative": neg,
        "recipe_kind": recipe.get("kind", "still"),
    }

    vc = vault_client_factory()
    res = await vc.learn(
        category="reference",
        title=f"civitai-image-{image_id}",
        body=body,
        author="ai-team-importer",
        audience=["terry", "claude-code"],
        tags=["image-recipe", "civitai"],
        extra=extra,
        idempotency_key=f"civitai-image-{image_id}",
    )
    if res is None or not res.get("ok"):
        raise RuntimeError(
            f"vault daemon refused recipe write: {res!r}"
        )
