"""FastAPI application factory for the Knowledge Management API."""

from __future__ import annotations

from fastapi import FastAPI

from src.routes.knowledge_routes import router as knowledge_router


def create_app() -> FastAPI:
    app = FastAPI(title="Knowledge Management API")
    app.include_router(knowledge_router)
    return app


app = create_app()
