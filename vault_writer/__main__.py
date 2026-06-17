"""Entrypoint: `python -m vault_writer`."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

import httpx

from vault_writer.config import load_config
from vault_writer.daemon import Daemon
from vault_writer.embedder import Embedder


async def amain(config_path: Path) -> int:
    cfg = load_config(config_path)

    log_dir = cfg.vault_path / ".vault-writer"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_dir / "daemon.log",
                maxBytes=5_000_000,
                backupCount=5,
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )

    async with httpx.AsyncClient(base_url=cfg.ollama_url) as client:
        embedder = Embedder(client=client, model=cfg.embedding_model,
                            dimension=cfg.embedding_dimension)
        daemon = Daemon(cfg, embedder=embedder)
        await daemon.start()

        stop = asyncio.Event()

        def _handle_signal(*_args: object) -> None:
            stop.set()

        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _handle_signal)
                except NotImplementedError:
                    pass
            await stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            await daemon.stop()
    return 0


def main() -> int:
    default_config = (
        Path(__file__).resolve().parent.parent / "config" / "vault-writer.yaml"
    )
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = default_config
    return asyncio.run(amain(path))


if __name__ == "__main__":
    raise SystemExit(main())
