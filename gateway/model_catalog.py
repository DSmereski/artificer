"""Model Catalog — registry of every Ollama model the hive may use.

Loaded from `config/model_catalog.yaml` on gateway startup. Refreshed
against `ollama list` so missing models flag themselves as unavailable.

The HiveCoordinator (M2.3) consults the catalog to:
  - Map a helper role -> the right model
  - Pick GPU vs CPU+RAM execution based on current VRAM pressure
  - Refuse to dispatch a helper whose configured model isn't pulled
    (loudly, with a remediation hint — never silent fallback to a
    smaller model)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("gateway.model_catalog")


@dataclass(frozen=True)
class ModelEntry:
    id: str                          # short ID used by helpers
    ollama_name: str | None = None   # the actual `ollama pull` name; None for cloud-only models
    family: str = "unknown"
    gpu_vram_mb: int = 0             # 0 for cloud models
    cpu_ram_mb: int | None = None    # None = cannot run on CPU (or cloud)
    cpu_fallback: bool = False
    speciality: str = ""
    use_for: tuple[str, ...] = ()    # helper roles this model can serve
    params: dict[str, Any] = field(default_factory=dict)
    # Cloud-API support (new in Task 3)
    cloud_provider: str | None = None       # e.g. "anthropic"
    cloud_model_name: str | None = None     # provider-specific model id
    cost_per_1k_tokens_input: float = 0.0
    cost_per_1k_tokens_output: float = 0.0


@dataclass(frozen=True)
class HelperEntry:
    role: str
    model: str                       # ModelEntry.id
    system_prompt_file: str
    output_schema: str               # name of the dataclass in gateway.helpers.shapes
    timeout_s: int
    # Optional per-helper Ollama params override. Merged on top of the
    # model's params (so e.g. the planner can use num_predict=2048 even
    # though planner-qwen defaults to 1024).
    params_override: tuple[tuple[str, Any], ...] = ()
    # Ordered list of candidate model ids the router may pick from.
    # Defaults to (model,) — single-candidate for back-compat.
    candidates: tuple[str, ...] = ()


@dataclass
class RefreshReport:
    available: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class ModelCatalog:
    """Static registry + Ollama liveness check."""

    def __init__(
        self,
        models: dict[str, ModelEntry],
        helpers: dict[str, HelperEntry],
    ) -> None:
        self._models = models
        self._helpers = helpers
        # Per-model availability flag, updated by refresh_from_ollama().
        # Default True so unit tests don't need a live Ollama.
        self._available: dict[str, bool] = {mid: True for mid in models}
        # Per-role runtime model override (#479). Populated from disk via
        # `apply_overrides()`; mutated by `set_override()` /
        # `clear_override()`. `helper(role)` consults this map and
        # returns a clone of the YAML-defined entry with `model` swapped.
        self._overrides: dict[str, str] = {}
        self._overrides_path: Path | None = None

    # ---------------------------------------------------------------- read

    @property
    def model_ids(self) -> list[str]:
        return list(self._models.keys())

    @property
    def helper_roles(self) -> list[str]:
        return list(self._helpers.keys())

    def model(self, model_id: str) -> ModelEntry:
        try:
            return self._models[model_id]
        except KeyError as e:
            raise KeyError(f"unknown model id: {model_id!r}") from e

    def helper(self, role: str) -> HelperEntry:
        try:
            base = self._helpers[role]
        except KeyError as e:
            raise KeyError(f"unknown helper role: {role!r}") from e
        override = self._overrides.get(role)
        if override is None:
            return base
        # Override is validated on set; defensively fall back to YAML if
        # the override model has somehow vanished from the catalog.
        if override not in self._models:
            log.warning(
                "stale helper override for %r -> %r (model not in catalog); "
                "ignoring override",
                role, override,
            )
            return base
        # Make sure the override model leads `candidates` so the router's
        # bench-rank selection picks it; preserve any other configured
        # candidates after it.
        new_candidates = (override,) + tuple(
            c for c in base.candidates if c != override
        )
        return replace(base, model=override, candidates=new_candidates)

    def get_override(self, role: str) -> str | None:
        return self._overrides.get(role)

    def set_override(self, role: str, model_id: str) -> None:
        if role not in self._helpers:
            raise KeyError(f"unknown helper role: {role!r}")
        if model_id not in self._models:
            raise ValueError(f"unknown model id: {model_id!r}")
        self._overrides[role] = model_id
        self._persist_overrides()

    def clear_override(self, role: str) -> None:
        if role in self._overrides:
            del self._overrides[role]
            self._persist_overrides()

    def attach_overrides_file(self, path: Path) -> None:
        """Wire a JSON file as the durable store for helper overrides.

        Reads + applies any existing entries; subsequent
        `set_override` / `clear_override` calls write back to this path.
        """
        self._overrides_path = path
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("failed to read helper overrides at %s: %s", path, e)
            return
        if not isinstance(raw, dict):
            log.warning("helper overrides file %s is not a JSON object", path)
            return
        for role, model_id in raw.items():
            if (
                isinstance(role, str)
                and isinstance(model_id, str)
                and role in self._helpers
                and model_id in self._models
            ):
                self._overrides[role] = model_id
            else:
                log.warning(
                    "dropping stale helper override %r -> %r at load time",
                    role, model_id,
                )

    def _persist_overrides(self) -> None:
        if self._overrides_path is None:
            return
        # Best-effort durable write — atomic_write_json keeps the file
        # consistent across crashes; failure is logged but not raised so
        # an unwritable state_dir doesn't crash the route handler.
        try:
            from shared.atomic_write import atomic_write_json
            atomic_write_json(self._overrides_path, dict(self._overrides))
        except Exception as e:  # noqa: BLE001
            log.warning(
                "failed to persist helper overrides to %s: %s",
                self._overrides_path, e,
            )

    def models_for_role(self, role: str) -> list[ModelEntry]:
        """All models whose `use_for` mentions this role (in catalog order)."""
        return [m for m in self._models.values() if role in m.use_for]

    def candidates_for_role(self, role: str) -> list[ModelEntry]:
        """Return candidate ModelEntries for a role, in YAML declaration order.

        Falls back to a single-element list of the helper's primary model
        when no explicit candidates list is configured.
        """
        helper = self.helper(role)
        return [self.model(mid) for mid in helper.candidates]

    def is_available(self, model_id: str) -> bool:
        return self._available.get(model_id, False)

    # ---------------------------------------------------------------- ollama

    def refresh_from_ollama(self) -> RefreshReport:
        """Mark each model available/missing using the Ollama HTTP API
        (`GET /api/tags`).

        Uses the HTTP API instead of shelling out to `ollama list`: on
        Windows a hung `ollama` CLI child keeps the captured stdout pipe
        open, so `subprocess.run(timeout=...)` deadlocks forever joining
        its reader threads — the timeout never fires — and wedges gateway
        startup (observed 2026-06-14: startup frozen in `refresh_from_ollama`
        at `subprocess.communicate`). An HTTP read-timeout has no such
        failure mode.

        Logs WARN for missing entries — never silently disables.
        """
        report = RefreshReport()
        try:
            installed = _installed_from_api(_ollama_base_url())
        except Exception as e:  # noqa: BLE001
            log.warning("ollama /api/tags unreachable (%s); skipping "
                        "availability refresh", e)
            return report

        for mid, entry in self._models.items():
            if entry.cloud_provider is not None:
                # Cloud models don't appear in `ollama list`; treat as available.
                # Real availability surfaces at API-call time.
                self._available[mid] = True
                report.available.append(mid)
                continue
            if entry.ollama_name is None:
                # Config error: non-cloud model without ollama_name — skip defensively.
                log.warning(
                    "catalog model %r has no ollama_name and no cloud_provider; "
                    "skipping availability check",
                    mid,
                )
                continue
            available = _ollama_name_present(installed, entry.ollama_name)
            self._available[mid] = available
            if available:
                report.available.append(mid)
            else:
                report.missing.append(mid)
                log.warning(
                    "catalog model %r (ollama: %s) NOT pulled — "
                    "helpers using it will fail. Fix: `ollama pull %s`",
                    mid, entry.ollama_name, entry.ollama_name,
                )
        return report

    # ---------------------------------------------------------------- prompt

    def render_for_hive_prompt(self) -> str:
        """Markdown summary suitable for embedding in Hive's system prompt.

        Caps to ~2000 chars so it doesn't dominate the context window.
        """
        lines: list[str] = ["## Available helpers (your hive)"]
        for h in self._helpers.values():
            try:
                m = self._models[h.model]
            except KeyError:
                continue
            available = " (UNAVAILABLE)" if not self.is_available(h.model) else ""
            model_ref = m.cloud_model_name or m.ollama_name or m.id
            lines.append(
                f"- **{h.role}** -> {model_ref} ({m.speciality}){available}"
            )
        out = "\n".join(lines)
        return out[:2000]


# ------------------------------------------------------------------- loaders


def _ollama_base_url() -> str:
    """Ollama HTTP base URL from `OLLAMA_HOST` (default local)."""
    host = (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def _installed_from_api(base_url: str, timeout: float = 10.0) -> set[str]:
    """Installed model names from Ollama's `GET /api/tags`.

    Returns the same NAME set `_parse_ollama_list` produced from the CLI
    (e.g. ``{"qwen2.5:7b"}``), so `_ollama_name_present` matching is
    unchanged. The HTTP timeout actually fires, unlike the CLI subprocess.
    """
    req = urllib.request.Request(f"{base_url}/api/tags")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = json.load(resp)
    names: set[str] = set()
    for m in data.get("models", []):
        name = (m.get("name") or m.get("model") or "").strip()
        if name:
            names.add(name)
    return names


def _parse_ollama_list(stdout: str) -> set[str]:
    """Extract model NAME column from `ollama list` output.

    Output looks like:
        NAME                  ID            SIZE      MODIFIED
        qwen2.5:7b            abc123def     4.7 GB    2 days ago
        qwen2.5-coder:7b      def456abc     4.6 GB    1 week ago
    """
    names: set[str] = set()
    for i, raw in enumerate(stdout.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if i == 0 and line.upper().startswith("NAME"):
            continue
        # First whitespace-separated token is the name (incl. tag).
        name = line.split()[0]
        names.add(name)
    return names


def _ollama_name_present(installed: set[str], wanted: str) -> bool:
    """Match a configured ollama_name against the installed set.

    Allow `qwen2.5:7b` to match `qwen2.5:7b` exactly OR
    `qwen2.5:7b-instruct-q4_K_M` (suffixed variants). For exact match
    of bare names without tag, match either `name` or `name:latest`.
    """
    if wanted in installed:
        return True
    if ":" not in wanted and f"{wanted}:latest" in installed:
        return True
    # Tag variant tolerance: `qwen2.5:7b` matches `qwen2.5:7b-anything`.
    for inst in installed:
        if inst.startswith(wanted) and (
            len(inst) == len(wanted) or inst[len(wanted)] in ("-", "_")
        ):
            return True
    return False


def load_catalog(yaml_path: Path) -> ModelCatalog:
    """Load + validate a catalog YAML file."""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    models: dict[str, ModelEntry] = {}
    for m in raw.get("models", []):
        ollama_name = m.get("ollama_name")
        cloud_provider = m.get("cloud_provider")
        cloud_model_name = m.get("cloud_model_name")
        # Validate: must have either ollama_name or cloud_provider+cloud_model_name.
        if not ollama_name and not (cloud_provider and cloud_model_name):
            raise ValueError(
                f"model {m.get('id')!r}: must have either ollama_name or "
                f"cloud_provider+cloud_model_name"
            )
        entry = ModelEntry(
            id=m["id"],
            ollama_name=ollama_name,
            family=m.get("family", "unknown"),
            gpu_vram_mb=int(m.get("gpu_vram_mb", 0)),
            cpu_ram_mb=int(m["cpu_ram_mb"]) if m.get("cpu_ram_mb") is not None else None,
            cpu_fallback=bool(m.get("cpu_fallback", False)),
            speciality=m.get("speciality", ""),
            use_for=tuple(m.get("use_for", [])),
            params=dict(m.get("params") or {}),
            cloud_provider=cloud_provider,
            cloud_model_name=cloud_model_name,
            cost_per_1k_tokens_input=float(m.get("cost_per_1k_tokens_input", 0.0)),
            cost_per_1k_tokens_output=float(m.get("cost_per_1k_tokens_output", 0.0)),
        )
        if entry.id in models:
            raise ValueError(f"duplicate model id: {entry.id!r}")
        models[entry.id] = entry

    helpers: dict[str, HelperEntry] = {}
    for h in raw.get("helpers", []):
        params_override_dict = dict(h.get("params") or {})
        role = h["role"]
        primary_model = h["model"]
        # Validate primary model first (preserves original error message).
        if primary_model not in models:
            raise ValueError(
                f"helper {role!r} references unknown model {primary_model!r}"
            )
        candidates = tuple(h.get("candidates") or ())
        if not candidates:
            candidates = (primary_model,)
        # Validate every candidate id resolves to a known model.
        for cid in candidates:
            if cid not in models:
                raise ValueError(
                    f"helper {role!r}: unknown candidate model {cid!r}"
                )
        entry = HelperEntry(
            role=role,
            model=primary_model,
            system_prompt_file=h["system_prompt_file"],
            output_schema=h.get("output_schema", "dict"),
            timeout_s=int(h.get("timeout_s", 30)),
            params_override=tuple(params_override_dict.items()),
            candidates=candidates,
        )
        if entry.role in helpers:
            raise ValueError(f"duplicate helper role: {entry.role!r}")
        helpers[entry.role] = entry

    return ModelCatalog(models=models, helpers=helpers)
