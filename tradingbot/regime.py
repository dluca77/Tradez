"""Detects the current market regime per instrument from OHLCV data."""
from __future__ import annotations

import pandas as pd

from tradingbot.indicators import adx, atr, ema
from tradingbot.models import MarketRegime


def detect_regime(df: pd.DataFrame, spread_pips: float, avg_spread_pips: float) -> MarketRegime:
    """df must have at least ~60 rows of OHLCV on the execution timeframe."""
    if len(df) < 30:
        return MarketRegime.UNPREDICTABLE

    close = df["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    adx_series = adx(df, 14)
    atr_series = atr(df, 14)

    last_adx = float(adx_series.iloc[-1])
    last_atr = float(atr_series.iloc[-1])
    atr_pct_of_price = last_atr / close.iloc[-1] if close.iloc[-1] else 0.0
    atr_history = atr_series.tail(50)
    atr_rank = (atr_history <= last_atr).mean() if len(atr_history) else 0.5

    if spread_pips > avg_spread_pips * 2.5:
        return MarketRegime.LOW_LIQUIDITY

    trending_up = ema20.iloc[-1] > ema50.iloc[-1] and close.iloc[-1] > ema20.iloc[-1]
    trending_down = ema20.iloc[-1] < ema50.iloc[-1] and close.iloc[-1] < ema20.iloc[-1]

    recent_range = (df["high"].tail(20).max() - df["low"].tail(20).min())
    price = close.iloc[-1]
    range_pct = recent_range / price if price else 0.0

    if last_adx >= 30 and trending_up:
        return MarketRegime.STRONG_UPTREND
    if last_adx >= 30 and trending_down:
        return MarketRegime.STRONG_DOWNTREND
    if last_adx >= 18 and (trending_up or trending_down):
        return MarketRegime.WEAK_TREND

    if atr_rank >= 0.90:
        return MarketRegime.HIGH_VOLATILITY
    if atr_rank <= 0.15:
        return MarketRegime.LOW_VOLATILITY

    if range_pct < 0.002 and last_adx < 15:
        return MarketRegime.CONSOLIDATION

    breakout = close.iloc[-1] > df["high"].tail(20).iloc[:-1].max() or close.iloc[-1] < df["low"].tail(20).iloc[:-1].min()
    if breakout:
        return MarketRegime.BREAKOUT

    if last_adx < 15:
        return MarketRegime.SIDEWAYS

    return MarketRegime.UNPREDICTABLE


REGIME_STRATEGY_MAP = {
    MarketRegime.STRONG_UPTREND: ["trend_following", "pullback"],
    MarketRegime.STRONG_DOWNTREND: ["trend_following", "pullback"],
    MarketRegime.WEAK_TREND: ["pullback", "momentum_scalping"],
    MarketRegime.SIDEWAYS: ["mean_reversion", "support_resistance", "vwap_reversion"],
    MarketRegime.CONSOLIDATION: ["mean_reversion", "support_resistance"],
    MarketRegime.BREAKOUT: ["breakout", "breakout_retest", "volatility_breakout"],
    MarketRegime.HIGH_VOLATILITY: ["volatility_breakout"],
    MarketRegime.LOW_VOLATILITY: ["mean_reversion"],
    MarketRegime.LOW_LIQUIDITY: [],
    MarketRegime.NEWS_VOLATILITY: [],
    MarketRegime.UNPREDICTABLE: [],
}


def eligible_strategies(regime: MarketRegime) -> list[str]:
    return REGIME_STRATEGY_MAP.get(regime, [])
