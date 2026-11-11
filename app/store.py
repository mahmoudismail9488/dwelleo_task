"""SQLite persistence (stdlib sqlite3, no ORM). Works with a file path or ':memory:'."""

import json
import sqlite3
import threading
from datetime import UTC, datetime

from app.models import Listing, MessageResult, RawMessage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    sender       TEXT,
    received_at  TEXT,
    text         TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,   -- insertion order for dedup
    id            TEXT NOT NULL UNIQUE,
    message_id    TEXT NOT NULL REFERENCES messages(id),
    property_type TEXT,
    district      TEXT,
    duplicate_of  TEXT,
    needs_review  INTEGER NOT NULL,
    approved_at   TEXT,
    listing_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_listings_review ON listings(needs_review);
CREATE INDEX IF NOT EXISTS ix_listings_match ON listings(property_type, district);
CREATE TABLE IF NOT EXISTS llm_runs (           -- one row per LLM call (D11)
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       TEXT NOT NULL,
    model            TEXT NOT NULL,
    status           TEXT NOT NULL,            -- ok | error | invalid_output | unusable
    error            TEXT,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    input_cost_usd   REAL,
    output_cost_usd  REAL,
    total_cost_usd   REAL,
    latency_s        REAL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_llm_runs_message ON llm_runs(message_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # --- messages ---

    def get_result(self, message_id: str) -> MessageResult | None:
        row = self._conn.execute(
            "SELECT result_json FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return MessageResult.model_validate_json(row["result_json"]) if row else None

    def save(self, message: RawMessage, result: MessageResult) -> None:
        """Store a message and its listings in one transaction."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages (id, sender, received_at, text, result_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message.id, message.sender, message.received_at, message.text,
                 result.model_dump_json(), _now()),
            )
            for listing in result.listings:
                self._conn.execute(
                    "INSERT INTO listings (id, message_id, property_type, district, duplicate_of, "
                    "needs_review, listing_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (listing.id, message.id, listing.property_type, listing.district,
                     listing.duplicate_of, int(listing.needs_review), listing.model_dump_json()),
                )

    # --- listings ---

    def dedup_candidates(self, listing: Listing, exclude_message_id: str) -> list[Listing]:
        """Stored listings that could match: same type and district, in insertion order."""
        if listing.property_type is None or listing.district is None:
            return []
        rows = self._conn.execute(
            "SELECT listing_json FROM listings WHERE property_type = ? AND district = ? "
            "AND message_id != ? ORDER BY seq",
            (listing.property_type, listing.district, exclude_message_id),
        ).fetchall()
        return [Listing.model_validate_json(r["listing_json"]) for r in rows]

    def _view(self, row: sqlite3.Row) -> dict:
        data = json.loads(row["listing_json"])
        data["message_id"] = row["message_id"]
        data["needs_review"] = bool(row["needs_review"])
        data["approved_at"] = row["approved_at"]
        return data

    def list_listings(self, needs_review: bool | None = None) -> list[dict]:
        sql = "SELECT * FROM listings"
        params: tuple = ()
        if needs_review is not None:
            sql += " WHERE needs_review = ?"
            params = (int(needs_review),)
        rows = self._conn.execute(sql + " ORDER BY seq", params).fetchall()
        return [self._view(r) for r in rows]

    # --- llm runs ---

    def record_llm_run(self, message_id: str, run) -> None:
        """Store one LLM call's metrics (`run` is an app.extractors.llm.LlmRun)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO llm_runs (message_id, model, status, error, input_tokens, output_tokens, "
                "input_cost_usd, output_cost_usd, total_cost_usd, latency_s, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, run.model, run.status, run.error, run.input_tokens, run.output_tokens,
                 run.input_cost_usd, run.output_cost_usd, run.total_cost_usd, run.latency_s, _now()),
            )

    def list_llm_runs(self, message_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM llm_runs", ()
        if message_id is not None:
            sql, params = sql + " WHERE message_id = ?", (message_id,)
        rows = self._conn.execute(sql + " ORDER BY id", params).fetchall()
        return [dict(r) for r in rows]

    def llm_summary(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS calls, SUM(status = 'ok') AS ok, "
            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(total_cost_usd) AS total_cost_usd, AVG(total_cost_usd) AS mean_cost_usd, "
            "AVG(latency_s) AS mean_latency_s, MAX(latency_s) AS max_latency_s FROM llm_runs"
        ).fetchone()
        return {k: (row[k] if row[k] is not None else 0) for k in row.keys()}

    def approve(self, listing_id: str) -> dict | None:
        """Mark a listing approved: needs_review → false. Review reasons are kept as history."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE listings SET needs_review = 0, approved_at = ? WHERE id = ?",
                (_now(), listing_id),
            )
            if cur.rowcount == 0:
                return None
        row = self._conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        return self._view(row)
