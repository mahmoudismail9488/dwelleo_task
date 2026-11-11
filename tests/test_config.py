import os

from app.config import load_env
from app.extractors.llm import LlmExtractor, default_extractor
from app.extractors.rules import RuleExtractor


def test_no_env_file_means_rule_extractor():
    assert load_env() is None
    assert isinstance(default_extractor(), RuleExtractor)


def test_env_file_is_loaded_and_enables_the_llm(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nLLM_API_KEY=from-file\nLLM_MODEL=\"qwen-from-file\"\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env))
    assert load_env() == env
    assert os.environ["LLM_API_KEY"] == "from-file"
    ex = default_extractor()
    assert isinstance(ex, LlmExtractor) and ex.model == "qwen-from-file"


def test_shell_value_wins_over_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=qwen-from-file\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env))
    monkeypatch.setenv("LLM_MODEL", "qwen-from-shell")
    load_env()
    assert os.environ["LLM_MODEL"] == "qwen-from-shell"


def test_env_file_with_empty_key_still_uses_rules(tmp_path, monkeypatch):
    # .env copied from .env.example but not filled in.
    env = tmp_path / ".env"
    env.write_text("LLM_API_KEY=\nLLM_MODEL=qwen-plus\n", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env))
    load_env()
    assert isinstance(default_extractor(), RuleExtractor)


def test_suite_never_sees_the_developers_real_env_file():
    assert not os.environ.get("LLM_API_KEY")
    assert os.environ["ENV_FILE"].endswith("no-such.env")
