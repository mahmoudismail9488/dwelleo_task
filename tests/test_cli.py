"""CLI contract: stdout = one JSON result per valid line; everything else on stderr."""

import json
import subprocess
import sys
from pathlib import Path

from app.cli import build_parser, main

FIXTURES = Path(__file__).parent / "fixtures" / "messages.jsonl"
CONTRACT_KEYS = {"id", "purpose", "property_type", "city", "district", "bedrooms", "bathrooms",
                 "area_sqm", "price", "contact_phone", "needs_review", "review_reasons", "duplicate_of"}


def run(capsys, path, *extra):
    code = main(["process", str(path), *extra])
    out, err = capsys.readouterr()
    return code, [json.loads(l) for l in out.splitlines()], err


def test_args():
    args = build_parser().parse_args(["process", "messages.jsonl"])
    assert (args.input, args.db) == ("messages.jsonl", ":memory:")


def test_one_contract_json_line_per_message(capsys):
    code, results, err = run(capsys, FIXTURES)
    assert code == 0
    assert [r["message_id"] for r in results] == [f"m-{i:03d}" for i in range(1, 11)]
    for r in results:
        assert set(r) >= {"message_id", "classification", "listings"}
        for listing in r["listings"]:
            assert CONTRACT_KEYS <= set(listing)
            assert set(listing["price"]) == {"amount", "currency", "period"}
    assert "processed 10 message(s), skipped 0" in err


def test_arabic_is_written_as_text_not_escapes(capsys, tmp_path):
    f = tmp_path / "in.jsonl"
    f.write_text(json.dumps({"id": "a", "text": "شقة للإيجار في حي الربوة ٤٠ ألف سنويا"},
                            ensure_ascii=False) + "\n", encoding="utf-8")
    main(["process", str(f)])
    out = capsys.readouterr().out
    assert "الربوة" in out and "\\u" not in out


def test_malformed_lines_go_to_stderr_and_do_not_stop_the_run(capsys, tmp_path):
    f = tmp_path / "in.jsonl"
    f.write_text("\n".join([
        '{"id":"ok-1","text":"Villa for sale in Al Malqa 3.2M"}',
        "this is not json",
        '{"text":"missing id"}',
        "",
        '{"id":"ok-2","text":"صباح الخير"}',
    ]), encoding="utf-8")
    code, results, err = run(capsys, f)
    assert code == 0
    assert [r["message_id"] for r in results] == ["ok-1", "ok-2"]
    assert "line 2: skipped" in err and "line 3: skipped" in err
    assert "processed 2 message(s), skipped 2" in err


def test_missing_input_file_exits_2(capsys, tmp_path):
    code = main(["process", str(tmp_path / "nope.jsonl")])
    out, err = capsys.readouterr()
    assert code == 2 and out == "" and "cannot read" in err


def test_db_option_persists_results(capsys, tmp_path):
    db = tmp_path / "out.db"
    run(capsys, FIXTURES, "--db", str(db))
    from app.store import Store
    assert len(Store(str(db)).list_listings()) == 9


def test_installed_command_keeps_stdout_clean():
    # Real process: stdout must parse line by line even with stderr output present.
    proc = subprocess.run([sys.executable, "-m", "app.cli", "process", str(FIXTURES)],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    lines = proc.stdout.splitlines()
    assert len(lines) == 10 and all(json.loads(l) for l in lines)
    assert "processed 10" in proc.stderr
