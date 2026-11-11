.PHONY: install test serve

# Load .env for the service if it exists. Tests never load it: they must run offline, no key.
ENV_FILE := $(if $(wildcard .env),--env-file .env,)

install:
	uv sync

test:
	uv run pytest -q

serve:
	uv run $(ENV_FILE) uvicorn app.api:app --host 127.0.0.1 --port 8000
