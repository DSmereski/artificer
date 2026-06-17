"""Runtime adapter contract.

Each adapter manages one local backend (ollama, comfy, embed, i2v) and
exposes a uniform `probe / start / stop / run` interface. Phase 2 ships
only `ollama`; Phase 3 lands the rest.

Adapters are keyed on a short name in a process-local registry. The
worker loop looks up the adapter by `kind.split(".")[0]` (e.g.
'ollama.generate' -> 'ollama'). Adapters do not own threads or
processes outside their own start/stop lifecycle — they're plain Python
objects whose methods are async.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Literal


RuntimeStatus = Literal["done", "error"]


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: RuntimeStatus
    output: dict[str, Any]
    duration_ms: int
    error: str = ""


class RuntimeAdapter(abc.ABC):
    """Contract every runtime adapter must satisfy."""

    name: str = ""  # subclasses must override

    @abc.abstractmethod
    async def probe(self) -> dict[str, Any]:
        """Return capability metadata: {installed: bool, version: str, ...}."""
        ...

    @abc.abstractmethod
    async def start(self) -> None:
        """Bring the runtime up. No-op if already running."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Tear the runtime down (best-effort)."""
        ...

    @abc.abstractmethod
    async def run(self, payload: dict[str, Any]) -> RuntimeResult:
        """Execute a single job and return the result."""
        ...


# Process-local adapter registry. The worker loop populates this on
# startup; tests can register their own dummies.
RUNTIMES: dict[str, RuntimeAdapter] = {}


def register_adapter(adapter: RuntimeAdapter) -> None:
    if not adapter.name:
        raise ValueError("adapter.name must be a non-empty string")
    RUNTIMES[adapter.name] = adapter


def get_adapter(name: str) -> RuntimeAdapter:
    try:
        return RUNTIMES[name]
    except KeyError:
        raise KeyError(f"runtime adapter '{name}' not registered") from None


def adapter_for_kind(kind: str) -> RuntimeAdapter:
    """Resolve adapter by job kind. Convention: kind = '<adapter>.<verb>',
    e.g. 'ollama.generate' or 'comfy.txt2img'."""
    head, _, _ = kind.partition(".")
    return get_adapter(head)
