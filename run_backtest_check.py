"""Real-data backtest check.

Runs every registered strategy against real historical price data (via
Yahoo Finance) for every configured instrument, so we can see which
strategy/instrument combinations have an actual, provable edge instead of
one measured against the synthetic random-walk MockBroker.

Usage (on a machine with normal internet access):
    python run_backtest_check.py
"""
from __future__ import annotations

import sys

from tradingbot.backtest import run_backtest
from tradingbot.data_loader import YAHOO_TICKERS, load_from_yfinance, resample

INSTRUMENTS = list(YAHOO_TICKERS.keys())


def main() -> None:
    print(f"{'Instrument':<10} {'Trades':>7} {'WinRate':>8} {'TotalPnL':>10} {'ProfitFactor':>13}")
    print("-" * 55)

    overall_trades = 0
    overall_pnl = 0.0

    for instrument in INSTRUMENTS:
        try:
            df_exec = load_from_yfinance(instrument, period="60d", interval="5m")
        except Exception as exc:  # noqa: BLE001
            print(f"{instrument:<10} skipped ({exc})")
            continue

        if len(df_exec) < 100:
            print(f"{instrument:<10} skipped (only {len(df_exec)} bars returned)")
            continue

        df_ctx = resample(df_exec, "1h")

        result = run_backtest(instrument, df_exec, df_ctx)

        wins = [t.pnl for t in result.trades if t.pnl > 0]
        losses = [t.pnl for t in result.trades if t.pnl <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        n = len(result.trades)
        overall_trades += n
        overall_pnl += result.total_pnl

        print(
            f"{instrument:<10} {n:>7} {result.win_rate*100:>7.1f}% "
            f"{result.total_pnl:>+10.2f} {profit_factor:>13.2f}"
        )

        # Per-strategy breakdown for this instrument
        by_strategy: dict[str, list] = {}
        for t in result.trades:
            by_strategy.setdefault(t.strategy, []).append(t)
        for strat, trades in sorted(by_strategy.items()):
            s_wins = sum(1 for t in trades if t.pnl > 0)
            s_pnl = sum(t.pnl for t in trades)
            s_avg_r = sum(t.r_multiple for t in trades) / len(trades)
            print(
                f"    - {strat:<20} trades={len(trades):>4} "
                f"win_rate={s_wins/len(trades)*100:>5.1f}% pnl={s_pnl:>+9.2f} avg_r={s_avg_r:>+6.2f}"
            )

    print("-" * 55)
    print(f"TOTAL: {overall_trades} trades, net pnl {overall_pnl:+.2f}")


if __name__ == "__main__":
    sys.exit(main())
