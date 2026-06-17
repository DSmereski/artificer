"""Minimal standalone Crew Board web server.

Mounts ONLY the board router against the live vault.db, so you can
view the kanban at http://localhost:8780/board while the dispatcher
driver runs separately. Read-mostly: SQLite WAL lets this read while
the driver writes. No models, no gateway stack.

Run:
    python scripts/serve_board.py        # then open http://localhost:8780/board
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import uvicorn
from fastapi import FastAPI

from gateway.crew_board.store import CrewBoardStore
from gateway.crew_board.notifications import CrewNotifier
from gateway.routes import board as board_route

VAULT_DB = Path(os.environ.get("HIVE_VAULT_PATH", "./vault")) / ".vault-writer" / "vault.db"
PORT = 8780


def build_app() -> FastAPI:
    app = FastAPI(title="Crew Board (standalone viewer)")
    app.state.crew_store = CrewBoardStore(VAULT_DB)
    app.state.crew_notifier = CrewNotifier()
    app.include_router(board_route.router)
    return app


if __name__ == "__main__":
    print(f"Crew Board viewer → http://localhost:{PORT}/board")
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
