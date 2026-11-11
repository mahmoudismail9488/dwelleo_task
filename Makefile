.PHONY: install test serve

install:
	uv sync

test:
	uv run pytest -q

serve:
	uv run uvicorn app.api:app --host 127.0.0.1 --port 8000
