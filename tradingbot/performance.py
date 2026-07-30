"""Performance Analytics: aggregates closed trades from the database."""
from __future__ import annotations

from dataclasses import dataclass

from tradingbot.database import Database

# Trades auto-closed by the recovery/reconciliation service (e.g. because the
# broker/session was restarted mid-trade and the position no longer exists at
# the broker) are not real trading outcomes — they're bookkeeping artifacts
# with a forced pnl of 0. Counting them would dilute the win rate and let
# restarts silently pad the trade count needed for the live-trading gate.
_ARTIFACT_EXIT_REASONS = {"reconciliation_broker_missing"}


def _real_trades(rows):
    return [r for r in rows if r["exit_reason"] not in _ARTIFACT_EXIT_REASONS]


@dataclass
class PerformanceSummary:
    total_trades: int
    win_rate: float
    total_pnl: float
    avg_r: float
    profit_factor: float
    by_instrument: dict[str, dict]
    by_strategy: dict[str, dict]


def _bucket_stats(rows, key: str) -> dict[str, dict]:
    buckets: dict[str, list] = {}
    for row in rows:
        buckets.setdefault(row[key], []).append(row)
    out = {}
    for name, trs in buckets.items():
        wins = [t for t in trs if (t["pnl"] or 0) > 0]
        out[name] = {
            "trades": len(trs),
            "win_rate": len(wins) / len(trs) if trs else 0.0,
            "total_pnl": sum((t["pnl"] or 0) for t in trs),
            "avg_r": sum((t["r_multiple"] or 0) for t in trs) / len(trs) if trs else 0.0,
        }
    return out


def compute_performance(db: Database) -> PerformanceSummary:
    rows = _real_trades(db.fetch_closed_trades())
    if not rows:
        return PerformanceSummary(0, 0.0, 0.0, 0.0, 0.0, {}, {})

    wins = [r for r in rows if (r["pnl"] or 0) > 0]
    losses = [r for r in rows if (r["pnl"] or 0) < 0]
    gross_win = sum((r["pnl"] or 0) for r in wins)
    gross_loss = abs(sum((r["pnl"] or 0) for r in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

    return PerformanceSummary(
        total_trades=len(rows),
        win_rate=len(wins) / len(rows),
        total_pnl=sum((r["pnl"] or 0) for r in rows),
        avg_r=sum((r["r_multiple"] or 0) for r in rows) / len(rows),
        profit_factor=pf,
        by_instrument=_bucket_stats(rows, "instrument"),
        by_strategy=_bucket_stats(rows, "strategy"),
    )


def historical_winrates(db: Database) -> dict[str, float]:
    rows = _real_trades(db.fetch_closed_trades())
    buckets: dict[str, list] = {}
    for row in rows:
        key = f"{row['instrument']}:{row['strategy']}"
        buckets.setdefault(key, []).append(row)
    return {
        key: sum(1 for r in trs if (r["pnl"] or 0) > 0) / len(trs)
        for key, trs in buckets.items()
        if len(trs) >= 5
    }
