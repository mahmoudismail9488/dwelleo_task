"""HTTP API. Endpoints arrive in build step 8 (PLAN.md §13)."""

from fastapi import FastAPI

app = FastAPI(title="Listing Inbox Normalizer")
