"""Selects which strategies are eligible for the current regime and runs them."""
from __future__ import annotations

import pandas as pd

from tradingbot.models import MarketRegime, StrategyName
from tradingbot.strategies.base import STRATEGY_REGISTRY, StrategyResult


def generate_signals(
    df_exec: pd.DataFrame,
    df_ctx: pd.DataFrame,
    regime: MarketRegime,
    strategy_params: dict[str, dict] | None = None,
) -> list[tuple[StrategyName, StrategyResult]]:
    """`strategy_params` maps strategy name -> its parameter overrides for
    the current instrument (e.g. {"trend_following": {"adx_threshold": 25}}).
    Every strategy runs for every instrument in every regime — the regime
    label is informational context for confidence scoring, never a hard
    gate on which strategies are allowed to even look at the candles.
    A strategy that finds nothing worth trading simply returns None on its
    own; that's a live read of the market, not a pre-set exclusion list."""
    strategy_params = strategy_params or {}
    results: list[tuple[StrategyName, StrategyResult]] = []
    for name, fn in STRATEGY_REGISTRY.items():
        res = fn(df_exec, df_ctx, strategy_params.get(name))
        if res is not None:
            results.append((StrategyName(name), res))
    return results
