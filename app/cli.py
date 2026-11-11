"""CLI entry point: `normalizer process <input.jsonl>`.

stdout carries only result JSON lines; everything else goes to stderr.
"""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="normalizer")
    sub = parser.add_subparsers(dest="command", required=True)
    process = sub.add_parser("process", help="process a .jsonl file of raw messages")
    process.add_argument("input", help="path to the input .jsonl file")
    process.add_argument("--db", default=":memory:", help="SQLite path (default: in-memory)")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    # Pipeline wiring arrives in build step 7 (PLAN.md §13).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
