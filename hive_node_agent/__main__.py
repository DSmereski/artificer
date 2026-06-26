"""`python -m hive_node_agent` — Phase 1 CLI.

Two modes:
  - `--host <url> --code <invite>`: pair (one-shot, then exit).
  - `--run`: start heartbeat loop using stored config (requires prior pair).

Phase 4 will replace this with the wizard server + Windows Service /
systemd unit. Phase 1 is dev-loop only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.heartbeat import run_heartbeat_loop
from hive_node_agent.pairing import pair_with_host
from hive_node_agent.probe import collect


def _default_state_dir() -> Path:
    return Path.home() / ".hive-node-agent"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="hive_node_agent")
    p.add_argument("--state-dir", type=Path, default=_default_state_dir())
    p.add_argument("--host", help="Host URL, e.g. http://127.0.0.1:8766")
    p.add_argument("--code", help="6-digit invite code (e.g. 814-273)")
    p.add_argument("--name", default="", help="Display name for this node")
    p.add_argument("--label", action="append", default=[], help="Repeatable")
    p.add_argument("--run", action="store_true", help="Start heartbeat loop")
    p.add_argument("--probe", action="store_true", help="Print snapshot and exit")
    return p.parse_args(argv)


async def _amain(argv: list[str]) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.probe:
        import json
        print(json.dumps(collect(labels=tuple(args.label)), indent=2))
        return 0

    cfg = NodeAgentConfig.load(args.state_dir)

    log = logging.getLogger("hive_node_agent")

    if args.host and args.code:
        snap = collect(labels=tuple(args.label))
        cfg = await pair_with_host(
            cfg,
            host_url=args.host,
            code=args.code,
            name=args.name or "unnamed-node",
            capabilities=snap,
        )
        log.info("paired: node_id=%s name=%s", cfg.node_id, cfg.name)
        if not args.run:
            return 0

    if args.run:
        if not cfg.paired:
            print("not paired — pass --host and --code first", file=sys.stderr)
            return 2

        # Register the ollama runtime adapter (Phase 2 ships only this one).
        from hive_node_agent.runtimes import register_adapter
        from hive_node_agent.runtimes.ollama import OllamaAdapter
        register_adapter(OllamaAdapter())

        def _capabilities_provider() -> dict:
            snap = collect(labels=tuple(args.label))
            runtimes = snap.get("runtimes", {})
            caps: set[str] = {
                name for name, info in runtimes.items()
                if isinstance(info, dict) and info.get("installed")
            }
            gpus = snap.get("gpus") or []
            vram_free_mb = max(
                (int(g.get("vram_free_mb") or 0) for g in gpus),
                default=0,
            )
            return {"caps": caps, "vram_free_mb": vram_free_mb}

        from hive_node_agent.worker import run_worker_loop

        try:
            await asyncio.gather(
                run_heartbeat_loop(cfg),
                run_worker_loop(cfg, capabilities_provider=_capabilities_provider),
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0

    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
