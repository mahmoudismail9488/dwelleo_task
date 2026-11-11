"""CLI entry point: `normalizer process <input.jsonl>`.

stdout carries only result JSON lines, one per valid input line. Everything else
(logs, malformed lines, a summary) goes to stderr. A bad line never stops the run.
"""

import argparse
import json
import logging
import sys

from pydantic import ValidationError

from app.config import load_env
from app.extractors.llm import default_extractor
from app.models import RawMessage
from app.pipeline import process_message
from app.store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="normalizer")
    sub = parser.add_subparsers(dest="command", required=True)
    process = sub.add_parser("process", help="process a .jsonl file of raw messages")
    process.add_argument("input", help="path to the input .jsonl file")
    process.add_argument("--db", default=":memory:", help="SQLite path (default: in-memory)")
    return parser


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def run_process(path: str, db: str) -> int:
    store = Store(db)
    extractor = default_extractor()
    _err(f"extractor: {extractor.name}" + ("" if extractor.name == "llm" else " (LLM_API_KEY not set)"))
    ok = bad = 0
    try:
        f = open(path, encoding="utf-8")
    except OSError as exc:
        _err(f"error: cannot read {path}: {exc}")
        return 2
    with f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                message = RawMessage.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError) as exc:
                bad += 1
                _err(f"line {line_no}: skipped, not a valid message: {str(exc).splitlines()[0]}")
                continue
            try:
                result = process_message(message, store, extractor)
            except Exception as exc:  # never let one message kill the run
                bad += 1
                _err(f"line {line_no} ({message.id}): processing failed: {exc!r}")
                continue
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False), flush=True)
            ok += 1
    _err(f"processed {ok} message(s), skipped {bad}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    load_env()
    if args.command == "process":
        return run_process(args.input, args.db)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
