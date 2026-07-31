"""Weekend deep-dive: backtest every strategy against every configured
instrument, across multiple execution timeframes, using real historical
data (Yahoo Finance) — to find which instrument + timeframe + strategy
combinations actually have a provable edge.

This is an extension of run_backtest_check.py: that one only tested M5.
This one also tests M15, since a strategy's edge can depend heavily on
which timeframe it's read on ("which candle reader" is best for it).

Takes a while (fetches + backtests ~2x more data than run_backtest_check.py).
Writes a full ranked CSV (weekend_analysis_results.csv) plus prints the
top and bottom 15 combinations to the console.

Usage (on a machine with normal internet access):
    python run_weekend_analysis.py
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass

from tradingbot.backtest import run_backtest
from tradingbot.data_loader import YAHOO_TICKERS, load_from_yfinance, resample

INSTRUMENTS = list(YAHOO_TICKERS.keys())
EXEC_TIMEFRAMES = ["5m", "15m"]  # yfinance intervals; both capped at ~60d history


@dataclass
class Row:
    instrument: str
    timeframe: str
    strategy: str
    trades: int
    win_rate: float
    total_pnl: float
    avg_r: float
    profit_factor: float


def main() -> None:
    rows: list[Row] = []

    for instrument in INSTRUMENTS:
        for timeframe in EXEC_TIMEFRAMES:
            try:
                df_exec = load_from_yfinance(instrument, period="60d", interval=timeframe)
            except Exception as exc:  # noqa: BLE001
                print(f"{instrument} {timeframe}: skipped ({exc})")
                continue

            if len(df_exec) < 100:
                print(f"{instrument} {timeframe}: skipped (only {len(df_exec)} bars)")
                continue

            df_ctx = resample(df_exec, "1h")
            result = run_backtest(instrument, df_exec, df_ctx)

            by_strategy: dict[str, list] = {}
            for t in result.trades:
                by_strategy.setdefault(t.strategy, []).append(t)

            for strat, trades in by_strategy.items():
                wins = [t.pnl for t in trades if t.pnl > 0]
                losses = [t.pnl for t in trades if t.pnl <= 0]
                gross_win = sum(wins)
                gross_loss = abs(sum(losses))
                profit_factor = (
                    gross_win / gross_loss if gross_loss > 0
                    else float("inf") if gross_win > 0 else 0.0
                )
                win_rate = len(wins) / len(trades) if trades else 0.0
                avg_r = sum(t.r_multiple for t in trades) / len(trades) if trades else 0.0
                rows.append(Row(
                    instrument=instrument, timeframe=timeframe, strategy=strat,
                    trades=len(trades), win_rate=win_rate,
                    total_pnl=sum(t.pnl for t in trades), avg_r=avg_r,
                    profit_factor=profit_factor,
                ))

            print(f"{instrument} {timeframe}: done ({len(result.trades)} trades)")

    # Only rank combinations with a reasonably meaningful sample size.
    ranked = sorted(
        (r for r in rows if r.trades >= 20),
        key=lambda r: r.profit_factor,
        reverse=True,
    )

    with open("weekend_analysis_results.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["instrument", "timeframe", "strategy", "trades", "win_rate", "total_pnl", "avg_r", "profit_factor"])
        for r in sorted(rows, key=lambda r: r.profit_factor, reverse=True):
            writer.writerow([r.instrument, r.timeframe, r.strategy, r.trades, f"{r.win_rate:.3f}", f"{r.total_pnl:.2f}", f"{r.avg_r:.3f}", f"{r.profit_factor:.3f}"])

    print("\n=== TOP 15 (trades >= 20) ===")
    print(f"{'Instrument':<10} {'TF':<5} {'Strategy':<20} {'Trades':>7} {'WinRate':>8} {'PnL':>10} {'AvgR':>7} {'PF':>6}")
    for r in ranked[:15]:
        print(f"{r.instrument:<10} {r.timeframe:<5} {r.strategy:<20} {r.trades:>7} {r.win_rate*100:>7.1f}% {r.total_pnl:>+10.2f} {r.avg_r:>+7.2f} {r.profit_factor:>6.2f}")

    print("\n=== BOTTOM 15 (trades >= 20) ===")
    for r in ranked[-15:]:
        print(f"{r.instrument:<10} {r.timeframe:<5} {r.strategy:<20} {r.trades:>7} {r.win_rate*100:>7.1f}% {r.total_pnl:>+10.2f} {r.avg_r:>+7.2f} {r.profit_factor:>6.2f}")

    print(f"\nFull results written to weekend_analysis_results.csv ({len(rows)} rows)")


if __name__ == "__main__":
    sys.exit(main())
