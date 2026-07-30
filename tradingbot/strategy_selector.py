"""Selects which strategies are eligible for the current regime and runs them."""
from __future__ import annotations

import pandas as pd

from tradingbot.models import MarketRegime, StrategyName
from tradingbot.regime import eligible_strategies
from tradingbot.strategies.base import STRATEGY_REGISTRY, StrategyResult


def generate_signals(
    df_exec: pd.DataFrame, df_ctx: pd.DataFrame, regime: MarketRegime
) -> list[tuple[StrategyName, StrategyResult]]:
    results: list[tuple[StrategyName, StrategyResult]] = []
    for name in eligible_strategies(regime):
        fn = STRATEGY_REGISTRY.get(name)
        if not fn:
            continue
        res = fn(df_exec, df_ctx)
        if res is not None:
            results.append((StrategyName(name), res))
    return results
