import pytest

from app.cli import build_parser


def test_process_requires_input_path():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["process"])


def test_process_defaults_to_in_memory_db():
    args = build_parser().parse_args(["process", "messages.jsonl"])
    assert args.input == "messages.jsonl"
    assert args.db == ":memory:"
