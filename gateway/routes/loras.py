"""/v1/loras — paste-URL LoRA importer + installed catalog.

User pastes a URL (Civitai .com/.red, HuggingFace, raw .safetensors).
Gateway parses, resolves, downloads, verifies, installs.

Endpoints:
  POST   /v1/loras/import          — start an import job
  GET    /v1/loras/import/{job_id} — poll job state
  GET    /v1/loras/imports         — recent jobs (this process)
  GET    /v1/loras                 — installed LoRAs from registry
  DELETE /v1/loras/{repo_id}       — remove from registry (file kept)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway import asset_importer, image_lora_doc
from gateway.deps import require_device, state, track_background_task


router = APIRouter(prefix="/v1/loras", tags=["loras"])
log = logging.getLogger("gateway.loras_route")


# ---------------------------------------------------------------- helpers


def _registry_paths(request: Request) -> tuple[Path, Path, Path]:
    """(loras_root, registry_path, checkpoints_root). Raises 503 if
    image_app_root not set."""
    st = state(request)
    cfg = st.config
    root = cfg.images.image_app_root
    if root is None:
        raise HTTPException(503, "image_app_root not configured")
    loras_root = root / "models" / "loras"
    registry = loras_root / "lora_registry.json"
    # Checkpoints land in the imageToVideo `community/` directory —
    # which the pipeline already scans on startup. No registry needed.
    checkpoints_root = root / "models" / "community"
    return loras_root, registry, checkpoints_root


def _check_import_rate_limit(request: Request, device_id: str) -> None:
    """Per-device rate limit on imports. Configured at app startup."""
    st = state(request)
    rl = st.rate_limiter
    if rl is None:
        return
    if not rl.try_acquire(device_id, "lora_imports"):
        raise HTTPException(
            status_code=429,
            detail="too many LoRA imports; back off",
        )


def _job_dict(job: asset_importer.ImportJob) -> dict:
    return {
        "id": job.id,
        "url": job.url,
        "state": job.state,
        "alias": job.alias,
        "repo_id": job.repo_id,
        "bytes_done": job.bytes_done,
        "bytes_total": job.bytes_total,
        "progress_pct": job.progress_pct,
        "dest_path": job.dest_path,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "sub_job_ids": list(job.sub_job_ids),
    }


def _store(request: Request) -> asset_importer.AssetImportStore:
    st = state(request)
    s = st.asset_import_store
    if s is None:
        raise HTTPException(503, "asset import store not initialised")
    return s


# ---------------------------------------------------------------- import


class ImportRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    # Optional: for Civitai image-page URLs the user pastes the prompt
    # block they see on the page (Civitai's API is locked, so we can't
    # fetch it server-side).
    pasted_text: str | None = Field(None, max_length=32_768)


@router.post("/import")
async def start_import(
    body: ImportRequest,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Start importing a LoRA from a URL. Returns the job record;
    poll `/v1/loras/import/{id}` for progress."""
    _check_import_rate_limit(request, device.id)

    parsed = asset_importer.parse_url(body.url)
    if parsed is None:
        raise HTTPException(
            400,
            "URL not recognised. Supported: civitai.com/.red model pages, "
            "huggingface.co/.../<file>.safetensors, raw .safetensors URLs.",
        )

    loras_root, registry, checkpoints_root = _registry_paths(request)
    s = _store(request)
    job = s.create(body.url)
    job.pasted_text = body.pasted_text

    # Recipe path needs the vault client to write the note. Build a
    # factory closure here so the runner stays free of FastAPI internals.
    st = state(request)

    def _vault_client_factory():
        existing = getattr(st, "vault_client", None)
        if existing is not None:
            return existing
        from shared.vault_client import VaultClient
        cfg = st.config
        return VaultClient(
            vault_path=cfg.vault_path,
            daemon_host=cfg.vault_writer.host,
            daemon_port=cfg.vault_writer.port,
        )

    # Closure that the importer can call to register sub-import tasks
    # (recipe paths can spawn one sub-job per LoRA URL in pasted text).
    def _track_sub_task(t):
        track_background_task(st, t)

    async def _runner():
        try:
            entry = await asset_importer.run_import(
                job, loras_root=loras_root, registry_path=registry,
                checkpoints_root=checkpoints_root,
                vault_client_factory=_vault_client_factory,
                asset_import_store=s,
                task_tracker=_track_sub_task,
            )
            if entry is None:
                # Publish event_bus + ntfy on errors so the user knows
                # something went wrong without having to check the app.
                bus = st.event_bus
                if bus is not None and job.error:
                    bus.publish({
                        "type": "import_done",
                        "kind": "error",
                        "url": job.url,
                        "error": job.error,
                    })
                try:
                    ntfy = st.ntfy
                    if ntfy is not None and ntfy.enabled and job.error:
                        await ntfy.publish(
                            topic="ai-team-loras",
                            title="Import failed",
                            message=f"{job.url[:80]} — {job.error[:160]}",
                            tags=["warning"],
                            priority=3,
                        )
                except Exception as e:  # noqa: BLE001
                    log.warning("ntfy import-fail publish failed: %s", e)
                return
            # Refresh canon doc so Hive's catalog mirrors the new entry.
            try:
                canon_path = st.config.vault_path / "knowledge" / "imagegen-loras.md"
                rewrote, n = image_lora_doc.regenerate_if_stale(
                    registry_path=registry, canon_path=canon_path,
                )
                if rewrote:
                    log.info(
                        "regenerated %s after import (%d LoRAs)",
                        canon_path, n,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("post-import canon refresh failed: %s", e)
            # Notify on success — image-recipe imports use a different
            # topic since they're a different mental model.
            is_recipe = entry.get("kind") == "image_recipe"
            alias = entry.get("alias") or entry.get("repo_id") or job.url

            bus = st.event_bus
            if bus is not None:
                bus.publish({
                    "type": "import_done",
                    "kind": "image_recipe" if is_recipe else "lora",
                    "alias": str(alias)[:200],
                    "repo_id": entry.get("repo_id", ""),
                })
            try:
                ntfy = st.ntfy
                if ntfy is not None and ntfy.enabled:
                    topic = "ai-team-recipes" if is_recipe else "ai-team-loras"
                    title = "Recipe saved" if is_recipe else "LoRA installed"
                    await ntfy.publish(
                        topic=topic,
                        title=title,
                        message=str(alias)[:200],
                        tags=["package"] if not is_recipe else ["bookmark"],
                        priority=2,
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("ntfy import-done publish failed: %s", e)
        except Exception:  # noqa: BLE001
            log.exception("import runner crashed for job %s", job.id)

    track_background_task(
        st, asyncio.create_task(_runner(), name=f"lora-import-{job.id}"),
    )
    return _job_dict(job)


@router.get("/import/{job_id}")
def get_import(
    job_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    job = s.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown import job: {job_id}")
    return _job_dict(job)


@router.get("/imports")
def list_imports(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    return {"jobs": [_job_dict(j) for j in s.list()]}


# ---------------------------------------------------------------- catalog


@router.get("")
def list_installed(
    pipeline: str | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Read the on-disk lora_registry.json. Source of truth for the
    image-gen pipeline; anything here will be candidate-selected by
    Hive's image director.

    Optional `?pipeline=` filter (case-insensitive substring on the
    entry's `pipeline` field). Used by Studio's LoRA picker to hide
    LoRAs incompatible with the user's selected base model.
    """
    _, registry, _ = _registry_paths(request)
    if not registry.is_file():
        return {"loras": []}
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(500, f"registry unreadable: {e}")
    if not isinstance(data, list):
        return {"loras": []}

    wanted = pipeline.strip().lower() if pipeline else None

    # Surface only the fields the app needs; keep payload small.
    out = []
    for e in data:
        if not isinstance(e, dict):
            continue
        entry_pipeline = str(e.get("pipeline", "unknown")).lower()
        if wanted and wanted not in entry_pipeline:
            continue
        out.append({
            "repo_id": e.get("repo_id", ""),
            "alias": e.get("alias", ""),
            "pipeline": entry_pipeline,
            "trigger_words": e.get("trigger_words", ""),
            "category": e.get("category", ""),
            "nsfw": bool(e.get("nsfw", False)),
            "default_strength": e.get("default_strength", 1.0),
            "main_file": e.get("main_file", ""),
        })
    return {"loras": out}


@router.get("/pipelines")
def list_pipelines(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Distinct pipeline values present in the installed LoRA registry.

    Powers Studio's base-model dropdown: each value here is a base
    model family (sdxl, sd15, flux, etc.) and selecting one filters
    the LoRA picker to compatible entries.
    """
    _, registry, _ = _registry_paths(request)
    if not registry.is_file():
        return {"pipelines": []}
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pipelines": []}
    if not isinstance(data, list):
        return {"pipelines": []}
    seen: set[str] = set()
    for e in data:
        if not isinstance(e, dict):
            continue
        p = str(e.get("pipeline", "")).strip().lower()
        if p and p != "unknown":
            seen.add(p)
    return {"pipelines": sorted(seen)}


@router.delete("/{repo_id:path}")
def remove_installed(
    repo_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Remove a LoRA from the registry. The file on disk is left
    in place (user can clean up manually)."""
    _, registry, _ = _registry_paths(request)
    if not registry.is_file():
        raise HTTPException(404, "registry not found")
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(500, f"registry unreadable: {e}")
    if not isinstance(data, list):
        raise HTTPException(500, "registry malformed")
    before = len(data)
    data = [e for e in data if not (
        isinstance(e, dict) and e.get("repo_id") == repo_id
    )]
    if len(data) == before:
        raise HTTPException(404, f"unknown repo_id: {repo_id}")
    registry.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Refresh canon doc.
    try:
        st = state(request)
        canon_path = st.config.vault_path / "knowledge" / "imagegen-loras.md"
        image_lora_doc.regenerate_if_stale(
            registry_path=registry, canon_path=canon_path,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("post-delete canon refresh failed: %s", e)
    return {"removed": repo_id, "remaining": len(data)}
