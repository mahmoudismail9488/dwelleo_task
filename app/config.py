"""Environment loading (DECISIONS.md D10).

Secrets and settings come from a `.env` file when it exists. Values already exported in
the shell win over the file. No `.env` and no LLM_API_KEY → the rule extractor is used.
The file path can be changed with ENV_FILE; the test suite points it at a missing file so
tests never read a developer's real key.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env() -> Path | None:
    """Load ENV_FILE (default `.env`) if it exists, without overriding the shell.
    Returns the loaded path, or None when there is no file."""
    path = Path(os.environ.get("ENV_FILE", ".env"))
    if not path.is_file():
        return None
    load_dotenv(path, override=False)
    return path
