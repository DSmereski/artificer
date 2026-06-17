"""Agent persistent config — host URL, node id, Bearer token, labels.

Stored at <state_dir>/agent.json. Atomic write so a crashed write does
not lose existing pairing state.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path


CONFIG_FILENAME = "agent.json"


@dataclass(frozen=True, slots=True)
class NodeAgentConfig:
    state_dir: Path
    host_url: str = ""
    token: str = ""
    node_id: str = ""
    name: str = ""
    labels: tuple[str, ...] = ()
    heartbeat_interval_s: int = 15

    @property
    def paired(self) -> bool:
        return bool(self.host_url and self.token and self.node_id)

    @classmethod
    def load(cls, state_dir: Path) -> "NodeAgentConfig":
        state_dir = Path(state_dir)
        path = state_dir / CONFIG_FILENAME
        if not path.is_file():
            return cls(state_dir=state_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(state_dir=state_dir)
        if not isinstance(data, dict):
            return cls(state_dir=state_dir)
        return cls(
            state_dir=state_dir,
            host_url=str(data.get("host_url", "")),
            token=str(data.get("token", "")),
            node_id=str(data.get("node_id", "")),
            name=str(data.get("name", "")),
            labels=tuple(str(x) for x in data.get("labels", ())),
            heartbeat_interval_s=int(data.get("heartbeat_interval_s", 15)),
        )

    def save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / CONFIG_FILENAME
        payload = {
            "host_url": self.host_url,
            "token": self.token,
            "node_id": self.node_id,
            "name": self.name,
            "labels": list(self.labels),
            "heartbeat_interval_s": self.heartbeat_interval_s,
        }
        # Atomic write: temp file in same dir + rename.
        fd, tmp = tempfile.mkstemp(prefix=".agent-", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def with_pairing(
        self,
        *,
        host_url: str,
        token: str,
        node_id: str,
        name: str,
        labels: tuple[str, ...] | None = None,
    ) -> "NodeAgentConfig":
        return replace(
            self,
            host_url=host_url,
            token=token,
            node_id=node_id,
            name=name,
            labels=labels if labels is not None else self.labels,
        )
