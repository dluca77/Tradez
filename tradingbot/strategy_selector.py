"""Selects which strategies are eligible for the current regime and runs them."""
from __future__ import annotations

import pandas as pd

from tradingbot.models import MarketRegime, StrategyName
from tradingbot.regime import eligible_strategies
from tradingbot.strategies.base import STRATEGY_REGISTRY, StrategyResult


def generate_signals(
    df_exec: pd.DataFrame,
    df_ctx: pd.DataFrame,
    regime: MarketRegime,
    strategy_params: dict[str, dict] | None = None,
) -> list[tuple[StrategyName, StrategyResult]]:
    """`strategy_params` maps strategy name -> its parameter overrides for
    the current instrument (e.g. {"trend_following": {"adx_threshold": 25}}).
    Every regime-eligible strategy still runs for every instrument — only
    its internal thresholds differ, never whether it's allowed to run."""
    strategy_params = strategy_params or {}
    results: list[tuple[StrategyName, StrategyResult]] = []
    for name in eligible_strategies(regime):
        fn = STRATEGY_REGISTRY.get(name)
        if not fn:
            continue
        res = fn(df_exec, df_ctx, strategy_params.get(name))
        if res is not None:
            results.append((StrategyName(name), res))
    return results
