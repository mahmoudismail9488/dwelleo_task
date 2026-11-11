.PHONY: install test serve

install:
	uv sync

# Offline, no API key: tests never read .env (tests/conftest.py).
test:
	uv run pytest -q

# Reads .env itself if present (app/config.py); without it, uses the rule extractor.
serve:
	uv run uvicorn app.api:app --host 127.0.0.1 --port 8000
