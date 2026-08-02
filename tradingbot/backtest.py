"""Backtesting Engine.

Replays historical OHLCV bar-by-bar (no look-ahead: strategies only ever see
data up to and including the current bar), applies spread/commission/slippage
costs, and simulates stop-loss / take-profit fills using intrabar high/low.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradingbot.broker.mock import INSTRUMENT_PROFILES
from tradingbot.costs import estimate_costs
from tradingbot.models import Direction, MarketRegime
from tradingbot.position_sizing import calculate_position_size
from tradingbot.regime import detect_regime
from tradingbot.strategy_selector import generate_signals


@dataclass
class BacktestTrade:
    instrument: str
    direction: str
    strategy: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    r_multiple: float
    opened_index: int
    closed_index: int


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)


def run_backtest(
    instrument: str,
    df_exec: pd.DataFrame,
    df_ctx: pd.DataFrame,
    starting_equity: float = 10_000.0,
    risk_pct: float = 0.0025,
    min_bars: int = 60,
    strategy_params: dict[str, dict] | None = None,
) -> BacktestResult:
    result = BacktestResult()
    equity = starting_equity
    profile = INSTRUMENT_PROFILES.get(instrument, {})
    spread = profile.get("spread_pips", 1.0) * profile.get("pip", 0.0001)

    open_trade: dict | None = None
    result.equity_curve.append(equity)

    for i in range(min_bars, len(df_exec)):
        window_exec = df_exec.iloc[: i + 1]
        window_ctx = df_ctx[df_ctx["time"] <= window_exec["time"].iloc[-1]] if "time" in df_ctx else df_ctx.iloc[: i + 1]
        bar = df_exec.iloc[i]

        if open_trade:
            direction_mult = 1 if open_trade["direction"] == Direction.LONG else -1
            hit_stop = (bar["low"] <= open_trade["stop_loss"]) if direction_mult == 1 else (bar["high"] >= open_trade["stop_loss"])
            hit_tp = (bar["high"] >= open_trade["take_profit"]) if direction_mult == 1 else (bar["low"] <= open_trade["take_profit"])
            exit_price = None
            if hit_stop:
                exit_price = open_trade["stop_loss"]
            elif hit_tp:
                exit_price = open_trade["take_profit"]
            if exit_price is not None:
                pnl = (exit_price - open_trade["entry_price"]) * direction_mult * open_trade["quantity"]
                costs = estimate_costs(instrument, open_trade["quantity"], spread)
                pnl -= costs.total
                equity += pnl
                r_mult = (exit_price - open_trade["entry_price"]) * direction_mult / abs(open_trade["entry_price"] - open_trade["initial_stop"])
                result.trades.append(BacktestTrade(
                    instrument=instrument, direction=open_trade["direction"].value, strategy=open_trade["strategy"],
                    entry_price=open_trade["entry_price"], exit_price=exit_price, quantity=open_trade["quantity"],
                    pnl=pnl, r_multiple=r_mult, opened_index=open_trade["opened_index"], closed_index=i,
                ))
                open_trade = None
                result.equity_curve.append(equity)
            continue

        if len(window_exec) < min_bars:
            continue

        spread_pips = spread / profile.get("pip", 0.0001) if profile.get("pip") else 0.0
        regime = detect_regime(window_exec, spread_pips, profile.get("spread_pips", 1.0))
        if regime in (MarketRegime.LOW_LIQUIDITY, MarketRegime.UNPREDICTABLE):
            continue

        signals = generate_signals(window_exec, window_ctx, regime, strategy_params)
        if not signals:
            continue

        strategy_name, sig = signals[0]
        sizing = calculate_position_size(instrument, equity, risk_pct, sig.entry_price, sig.stop_loss)
        if sizing.lots_or_units <= 0:
            continue

        open_trade = {
            "direction": sig.direction,
            "strategy": strategy_name.value,
            "entry_price": sig.entry_price,
            "stop_loss": sig.take_profits[0] if False else sig.stop_loss,
            "initial_stop": sig.stop_loss,
            "take_profit": sig.take_profits[0],
            "quantity": sizing.lots_or_units,
            "opened_index": i,
        }

    return result
