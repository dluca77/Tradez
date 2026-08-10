"""CLI: run a backtest for one instrument using historical data.

Usage:
    python run_backtest.py EURUSD --csv path/to/eurusd_m5.csv
    python run_backtest.py XAUUSD --yfinance --period 60d --interval 5m
"""
from __future__ import annotations

import argparse

from tradingbot.backtest import run_backtest
from tradingbot.data_loader import load_csv, load_from_yfinance, resample


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest one instrument.")
    parser.add_argument("instrument", help="e.g. EURUSD, XAUUSD, GER40")
    parser.add_argument("--csv", help="Path to a CSV with time,open,high,low,close[,volume] columns")
    parser.add_argument("--yfinance", action="store_true", help="Download data via yfinance instead of --csv")
    parser.add_argument("--period", default="60d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=0.0025)
    args = parser.parse_args()

    if args.csv:
        df_exec = load_csv(args.csv)
    elif args.yfinance:
        df_exec = load_from_yfinance(args.instrument, period=args.period, interval=args.interval)
    else:
        parser.error("Provide either --csv or --yfinance")
        return

    df_ctx = resample(df_exec, "1h")

    result = run_backtest(args.instrument, df_exec, df_ctx, starting_equity=args.equity, risk_pct=args.risk_pct)

    print(f"Instrument:      {args.instrument}")
    print(f"Bars used:       {len(df_exec)}")
    print(f"Trades:          {len(result.trades)}")
    print(f"Win rate:        {result.win_rate:.1%}")
    print(f"Total PnL:       {result.total_pnl:.2f}")
    print(f"Ending equity:   {result.equity_curve[-1]:.2f}")


if __name__ == "__main__":
    main()
