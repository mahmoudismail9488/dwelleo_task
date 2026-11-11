import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """The suite must run offline with no API key (task.md), even on a machine with a real
    .env or exported key: clear the variables and point ENV_FILE at a file that doesn't exist.
    Subprocesses inherit this environment too."""
    for var in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT_SECONDS", "DB_PATH",
                "LLM_INPUT_PRICE_PER_M", "LLM_OUTPUT_PRICE_PER_M"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "no-such.env"))
