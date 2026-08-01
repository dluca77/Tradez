"""Backtest using MT5's own historical data instead of Yahoo Finance.

Requires an MT5 terminal running and logged in (same setup as run_paper.py
with broker.name: mt5) — this pulls candles directly from the terminal via
the same MetaTrader5 package, which is your broker's actual historical
price/spread data rather than Yahoo Finance's free feed.

Before running, open a chart for each instrument at M5 in the MT5 terminal
at least once — that makes the terminal download/cache full history for it;
otherwise copy_rates_from_pos may only return a short recent window.

Usage:
    python run_mt5_backtest.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pandas as pd

from tradingbot.backtest import run_backtest
from tradingbot.broker.mt5 import MT5_SYMBOL_MAP, _TIMEFRAME_NAMES, _import_mt5
from tradingbot.config import env, load_config
from tradingbot.data_loader import resample

BARS_TO_FETCH = 20_000  # ~ a few months of M5 bars, depending on the instrument's session hours


def fetch_mt5_candles(mt5, symbol: str, timeframe_name: str, count: int) -> pd.DataFrame:
    tf_const = getattr(mt5, _TIMEFRAME_NAMES.get(timeframe_name, "TIMEFRAME_M5"))
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data returned for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    return pd.DataFrame({
        "time": [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in df["time"]],
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["tick_volume"].astype(float),
    })


def main() -> None:
    cfg = load_config()
    mt5 = _import_mt5()

    login, password, server = env("MT5_LOGIN"), env("MT5_PASSWORD"), env("MT5_SERVER")
    suffix = env("MT5_SYMBOL_SUFFIX", "") or ""

    ok = mt5.initialize()
    if not ok and login and password and server:
        ok = mt5.initialize(login=int(login), password=password, server=server)
    if not ok:
        print(f"MT5 initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    print(f"{'Instrument':<10} {'Trades':>7} {'WinRate':>8} {'TotalPnL':>10} {'ProfitFactor':>13}")
    print("-" * 55)

    overall_trades = 0
    overall_pnl = 0.0

    for instrument in MT5_SYMBOL_MAP:
        symbol = MT5_SYMBOL_MAP[instrument] + suffix
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"{instrument:<10} skipped (symbol '{symbol}' not found at this broker)")
            continue
        if not info.visible:
            mt5.symbol_select(symbol, True)

        try:
            df_exec = fetch_mt5_candles(mt5, symbol, "M5", BARS_TO_FETCH)
        except Exception as exc:  # noqa: BLE001
            print(f"{instrument:<10} skipped ({exc})")
            continue

        if len(df_exec) < 100:
            print(f"{instrument:<10} skipped (only {len(df_exec)} bars — open its M5 chart in MT5 first)")
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
            f"{result.total_pnl:>+10.2f} {profit_factor:>13.2f}  "
            f"(bars: {len(df_exec)}, {df_exec['time'].iloc[0].date()} -> {df_exec['time'].iloc[-1].date()})"
        )

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
    mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
