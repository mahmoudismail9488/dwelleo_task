"""HTTP API. Every endpoint goes through the same pipeline and store as the CLI."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request

from app.models import MessageResult, RawMessage
from app.pipeline import process_message
from app.store import Store


def _default_store() -> Store:
    path = os.environ.get("DB_PATH", "./data/listings.db")
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return Store(path)


def create_app(store: Store | None = None) -> FastAPI:
    app = FastAPI(title="Listing Inbox Normalizer")
    app.state.store = store  # the real DB file is opened lazily, on first request

    def get_store(request: Request) -> Store:
        if request.app.state.store is None:
            request.app.state.store = _default_store()
        return request.app.state.store

    @app.post("/messages", response_model=MessageResult)
    def ingest_message(message: RawMessage, request: Request) -> MessageResult:
        """Process one raw message and return its result. Re-posting an id returns the stored result."""
        return process_message(message, get_store(request))

    @app.get("/listings")
    def list_listings(
        request: Request,
        needs_review: bool | None = Query(default=None, description="filter by review status"),
    ) -> list[dict]:
        return get_store(request).list_listings(needs_review)

    @app.post("/listings/{listing_id}/approve")
    def approve_listing(listing_id: str, request: Request) -> dict:
        approved = get_store(request).approve(listing_id)
        if approved is None:
            raise HTTPException(status_code=404, detail=f"listing {listing_id} not found")
        return approved

    return app


app = create_app()
