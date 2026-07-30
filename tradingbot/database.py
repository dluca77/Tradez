"""SQLite persistence + audit logging for every autonomous decision."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    confidence REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL,
    risk_amount REAL NOT NULL,
    pnl REAL,
    r_multiple REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_reason TEXT,
    mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    instrument TEXT,
    decision_type TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    parameter TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS safety_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log_decision(self, decision_type: str, payload: dict[str, Any], instrument: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO decisions (ts, instrument, decision_type, payload) VALUES (?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), instrument, decision_type, json.dumps(payload, default=str)),
            )

    def log_parameter_change(self, parameter: str, old_value: Any, new_value: Any, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO parameter_changes (ts, parameter, old_value, new_value, reason) VALUES (?, ?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), parameter, str(old_value), str(new_value), reason),
            )

    def log_safety_event(self, event_type: str, detail: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO safety_events (ts, event_type, detail) VALUES (?, ?, ?)",
                (datetime.utcnow().isoformat(), event_type, detail),
            )

    def record_trade_open(self, trade: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO trades (id, instrument, direction, strategy, regime, confidence,
                   entry_price, quantity, stop_loss, take_profit, risk_amount, opened_at, mode)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade["id"], trade["instrument"], trade["direction"], trade["strategy"],
                    trade["regime"], trade["confidence"], trade["entry_price"], trade["quantity"],
                    trade["stop_loss"], trade.get("take_profit"), trade["risk_amount"],
                    trade["opened_at"], trade["mode"],
                ),
            )

    def record_trade_close(self, trade_id: str, exit_price: float, pnl: float, r_multiple: float, exit_reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE trades SET exit_price=?, pnl=?, r_multiple=?, closed_at=?, exit_reason=? WHERE id=?",
                (exit_price, pnl, r_multiple, datetime.utcnow().isoformat(), exit_reason, trade_id),
            )

    def fetch_closed_trades(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC")
            return cur.fetchall()

    def fetch_open_trades(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM trades WHERE closed_at IS NULL")
            return cur.fetchall()
