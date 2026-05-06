"""FastAPI application entry point."""
from __future__ import annotations

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.config import get_settings


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        stream=sys.stdout,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)

    app = FastAPI(
        title="Text-to-SQL Interface with Guardrails",
        version="0.1.0",
        description=(
            "Translate plain-English questions into safe SQL against a Postgres "
            "database. Generated queries pass through a guardrail layer that blocks "
            "destructive operations, are executed in a read-only transaction, and "
            "are validated by a hallucination-detection pipeline before results are "
            "returned with a confidence score."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "service": "text-to-sql-guardrails",
            "version": app.version,
            "docs": "/docs",
        }

    return app


app = create_app()
