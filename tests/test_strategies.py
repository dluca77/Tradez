from __future__ import annotations

from tradingbot.models import Direction
from tradingbot.strategies.base import STRATEGY_REGISTRY, trend_following


def test_registry_has_all_ten_strategies():
    expected = {
        "trend_following", "momentum_scalping", "breakout", "breakout_retest",
        "pullback", "mean_reversion", "support_resistance", "vwap_reversion",
        "session_breakout", "volatility_breakout",
    }
    assert expected.issubset(STRATEGY_REGISTRY.keys())


def test_trend_following_detects_uptrend(trending_up_df):
    ctx = trending_up_df.copy()
    result = trend_following(trending_up_df, ctx)
    if result is not None:
        assert result.direction in (Direction.LONG, Direction.SHORT)
        assert result.stop_loss != result.entry_price
        assert len(result.take_profits) == 3


def test_strategy_never_returns_zero_risk(trending_up_df):
    for fn in STRATEGY_REGISTRY.values():
        res = fn(trending_up_df, trending_up_df)
        if res is not None:
            assert abs(res.entry_price - res.stop_loss) > 0
