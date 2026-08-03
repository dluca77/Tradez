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

CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    consecutive_losses INTEGER NOT NULL,
    trades_today INTEGER NOT NULL,
    trades_this_hour INTEGER NOT NULL,
    hour_window_start TEXT NOT NULL,
    cooldown_until TEXT,
    kill_switch INTEGER NOT NULL,
    risk_scale REAL NOT NULL,
    day_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimization_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    min_confidence REAL NOT NULL,
    disabled_strategies TEXT NOT NULL,
    risk_multiplier REAL NOT NULL,
    last_evaluated_trades INTEGER NOT NULL
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

    def fetch_recent_decisions(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            )
            return cur.fetchall()

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

    def save_session_state(
        self,
        consecutive_losses: int,
        trades_today: int,
        trades_this_hour: int,
        hour_window_start: datetime,
        cooldown_until: datetime | None,
        kill_switch: bool,
        risk_scale: float,
        day_date,
    ) -> None:
        # Single-row upsert (id is pinned to 1) so risk/safety state survives
        # a bot restart instead of silently resetting to defaults — a reset
        # consecutive_losses/cooldown_until/kill_switch lets the bot bypass
        # a safety block that should still be in effect.
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO session_state
                   (id, consecutive_losses, trades_today, trades_this_hour, hour_window_start,
                    cooldown_until, kill_switch, risk_scale, day_date)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       consecutive_losses=excluded.consecutive_losses,
                       trades_today=excluded.trades_today,
                       trades_this_hour=excluded.trades_this_hour,
                       hour_window_start=excluded.hour_window_start,
                       cooldown_until=excluded.cooldown_until,
                       kill_switch=excluded.kill_switch,
                       risk_scale=excluded.risk_scale,
                       day_date=excluded.day_date""",
                (
                    consecutive_losses, trades_today, trades_this_hour,
                    hour_window_start.isoformat(),
                    cooldown_until.isoformat() if cooldown_until else None,
                    int(kill_switch), risk_scale, day_date.isoformat(),
                ),
            )

    def load_session_state(self) -> sqlite3.Row | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM session_state WHERE id = 1")
            return cur.fetchone()

    def save_optimization_state(
        self,
        min_confidence: float,
        disabled_strategies: set[str],
        risk_multiplier: float,
        last_evaluated_trades: int,
    ) -> None:
        # Same reasoning as save_session_state: without persisting this, a
        # restart silently reset risk_multiplier/min_confidence/disabled
        # strategies back to their defaults, discarding tightening the bot
        # had deliberately applied based on real historical performance.
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO optimization_state
                   (id, min_confidence, disabled_strategies, risk_multiplier, last_evaluated_trades)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       min_confidence=excluded.min_confidence,
                       disabled_strategies=excluded.disabled_strategies,
                       risk_multiplier=excluded.risk_multiplier,
                       last_evaluated_trades=excluded.last_evaluated_trades""",
                (
                    min_confidence, json.dumps(sorted(disabled_strategies)),
                    risk_multiplier, last_evaluated_trades,
                ),
            )

    def load_optimization_state(self) -> sqlite3.Row | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM optimization_state WHERE id = 1")
            return cur.fetchone()
