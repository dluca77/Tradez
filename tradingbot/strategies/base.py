"""Strategy interface + registry. Each strategy inspects execution-timeframe OHLCV
(plus higher-timeframe context) and returns a StrategyResult when it finds a
qualifying setup that uses multiple confirming indicators (never a single indicator).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from tradingbot.indicators import (
    adx,
    atr,
    bollinger_bands,
    ema,
    macd,
    nearest_support_resistance,
    rsi,
    vwap,
)
from tradingbot.models import Direction, StrategyName


@dataclass
class StrategyResult:
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profits: list[float]
    atr: float
    strength: float  # 0..1, contributes to confidence scoring
    reasons: list[str] = field(default_factory=list)


StrategyFn = Callable[[pd.DataFrame, pd.DataFrame], Optional[StrategyResult]]
STRATEGY_REGISTRY: dict[str, StrategyFn] = {}


def register(name: str):
    def deco(fn: StrategyFn) -> StrategyFn:
        STRATEGY_REGISTRY[name] = fn
        return fn
    return deco


def _rr_targets(entry: float, stop: float, direction: Direction) -> list[float]:
    risk = abs(entry - stop)
    mult = [1.0, 1.5, 2.5]
    if direction == Direction.LONG:
        return [entry + risk * m for m in mult]
    return [entry - risk * m for m in mult]


@register(StrategyName.TREND_FOLLOWING.value)
def trend_following(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    e9, e20, e50, e200 = ema(close, 9), ema(close, 20), ema(close, 50), ema(close, 200)
    adx_v = adx(df, 14).iloc[-1]
    atr_v = atr(df, 14).iloc[-1]
    ctx_trend_up = ctx["close"].iloc[-1] > ema(ctx["close"], 50).iloc[-1]
    ctx_trend_down = ctx["close"].iloc[-1] < ema(ctx["close"], 50).iloc[-1]

    reasons = []
    if e9.iloc[-1] > e20.iloc[-1] > e50.iloc[-1] > e200.iloc[-1] and adx_v > 22 and ctx_trend_up:
        reasons = ["EMA stack bullish", "ADX confirms trend", "HTF trend aligned"]
        entry = close.iloc[-1]
        stop = entry - 1.5 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, min(adx_v / 40, 1.0), reasons)
    if e9.iloc[-1] < e20.iloc[-1] < e50.iloc[-1] < e200.iloc[-1] and adx_v > 22 and ctx_trend_down:
        reasons = ["EMA stack bearish", "ADX confirms trend", "HTF trend aligned"]
        entry = close.iloc[-1]
        stop = entry + 1.5 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, min(adx_v / 40, 1.0), reasons)
    return None


@register(StrategyName.MOMENTUM_SCALPING.value)
def momentum_scalping(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    macd_line, signal_line, hist = macd(close)
    rsi_v = rsi(close, 14)
    atr_v = atr(df, 14).iloc[-1]
    momentum_up = hist.iloc[-1] > 0 and hist.iloc[-1] > hist.iloc[-2] and rsi_v.iloc[-1] > 55
    momentum_down = hist.iloc[-1] < 0 and hist.iloc[-1] < hist.iloc[-2] and rsi_v.iloc[-1] < 45
    entry = close.iloc[-1]
    if momentum_up and macd_line.iloc[-1] > signal_line.iloc[-1]:
        stop = entry - 1.0 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.6, ["MACD momentum up", "RSI > 55"])
    if momentum_down and macd_line.iloc[-1] < signal_line.iloc[-1]:
        stop = entry + 1.0 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.6, ["MACD momentum down", "RSI < 45"])
    return None


@register(StrategyName.BREAKOUT.value)
def breakout(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    atr_v = atr(df, 14).iloc[-1]
    recent_high = df["high"].iloc[-21:-1].max()
    recent_low = df["low"].iloc[-21:-1].min()
    vol_confirm = df["volume"].iloc[-1] > df["volume"].tail(20).mean()
    entry = close.iloc[-1]
    if entry > recent_high and vol_confirm:
        stop = entry - 1.2 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.65, ["Breakout above 20-bar high", "Volume confirms"])
    if entry < recent_low and vol_confirm:
        stop = entry + 1.2 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.65, ["Breakdown below 20-bar low", "Volume confirms"])
    return None


@register(StrategyName.BREAKOUT_RETEST.value)
def breakout_retest(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    atr_v = atr(df, 14).iloc[-1]
    level_window = df.iloc[-30:-5]
    recent_high = level_window["high"].max()
    recent_low = level_window["low"].min()
    last3 = df.tail(3)
    entry = close.iloc[-1]
    retested_high = (last3["low"] <= recent_high * 1.001).any() and entry > recent_high
    retested_low = (last3["high"] >= recent_low * 0.999).any() and entry < recent_low
    if retested_high:
        stop = recent_high - 1.0 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.7, ["Broke resistance", "Successful retest hold"])
    if retested_low:
        stop = recent_low + 1.0 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.7, ["Broke support", "Successful retest hold"])
    return None


@register(StrategyName.PULLBACK.value)
def pullback(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    e20, e50 = ema(close, 20), ema(close, 50)
    rsi_v = rsi(close, 14)
    atr_v = atr(df, 14).iloc[-1]
    entry = close.iloc[-1]
    uptrend = e20.iloc[-1] > e50.iloc[-1]
    downtrend = e20.iloc[-1] < e50.iloc[-1]
    pulled_to_ema = abs(entry - e20.iloc[-1]) < 0.5 * atr_v
    if uptrend and pulled_to_ema and 40 < rsi_v.iloc[-1] < 60:
        stop = e50.iloc[-1] - 0.5 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.65, ["Uptrend pullback to EMA20", "RSI neutral reset"])
    if downtrend and pulled_to_ema and 40 < rsi_v.iloc[-1] < 60:
        stop = e50.iloc[-1] + 0.5 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.65, ["Downtrend pullback to EMA20", "RSI neutral reset"])
    return None


@register(StrategyName.MEAN_REVERSION.value)
def mean_reversion(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    upper, mid, lower = bollinger_bands(close, 20, 2.0)
    rsi_v = rsi(close, 14)
    atr_v = atr(df, 14).iloc[-1]
    entry = close.iloc[-1]
    if entry <= lower.iloc[-1] and rsi_v.iloc[-1] < 30:
        stop = entry - 1.0 * atr_v
        target = mid.iloc[-1]
        return StrategyResult(Direction.LONG, entry, stop, [target, target + atr_v, target + 2 * atr_v], atr_v, 0.6, ["Price at lower BB", "RSI oversold"])
    if entry >= upper.iloc[-1] and rsi_v.iloc[-1] > 70:
        stop = entry + 1.0 * atr_v
        target = mid.iloc[-1]
        return StrategyResult(Direction.SHORT, entry, stop, [target, target - atr_v, target - 2 * atr_v], atr_v, 0.6, ["Price at upper BB", "RSI overbought"])
    return None


@register(StrategyName.SUPPORT_RESISTANCE.value)
def support_resistance(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    atr_v = atr(df, 14).iloc[-1]
    support, resistance = nearest_support_resistance(df)
    rsi_v = rsi(close, 14).iloc[-1]
    entry = close.iloc[-1]
    if support and abs(entry - support) < 0.4 * atr_v and rsi_v < 45:
        stop = support - 1.0 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.6, ["Bounce off support", "RSI supportive"])
    if resistance and abs(entry - resistance) < 0.4 * atr_v and rsi_v > 55:
        stop = resistance + 1.0 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.6, ["Rejection at resistance", "RSI supportive"])
    return None


@register(StrategyName.VWAP_REVERSION.value)
def vwap_reversion(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    vwap_v = vwap(df)
    atr_v = atr(df, 14).iloc[-1]
    entry = close.iloc[-1]
    dist = entry - vwap_v.iloc[-1]
    if dist < -1.5 * atr_v:
        stop = entry - 1.0 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, [vwap_v.iloc[-1], vwap_v.iloc[-1] + 0.5 * atr_v], atr_v, 0.55, ["Extended below VWAP", "Reversion expected"])
    if dist > 1.5 * atr_v:
        stop = entry + 1.0 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, [vwap_v.iloc[-1], vwap_v.iloc[-1] - 0.5 * atr_v], atr_v, 0.55, ["Extended above VWAP", "Reversion expected"])
    return None


@register(StrategyName.SESSION_BREAKOUT.value)
def session_breakout(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    atr_v = atr(df, 14).iloc[-1]
    session_window = df.tail(30)
    session_high = session_window["high"].max()
    session_low = session_window["low"].min()
    entry = close.iloc[-1]
    vol_confirm = df["volume"].iloc[-1] > df["volume"].tail(30).mean() * 1.2
    if entry >= session_high * 0.999 and vol_confirm:
        stop = entry - 1.2 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.6, ["Session range breakout up", "Volume surge"])
    if entry <= session_low * 1.001 and vol_confirm:
        stop = entry + 1.2 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.6, ["Session range breakout down", "Volume surge"])
    return None


@register(StrategyName.OPENING_RANGE_FVG.value)
def opening_range_fvg(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    """Opening-range breakout with a Fair Value Gap retest entry (an SMC/ICT
    concept: a 3-candle imbalance where candle 1 and candle 3 don't overlap).
    Range = the first M5 candle of the NY session (13:30 UTC). Requires a
    breakout beyond that range followed by a same-direction FVG to retest
    into, rather than chasing the breakout candle itself."""
    if "time" not in df.columns or len(df) < 10:
        return None

    times = df["time"]
    last_time = times.iloc[-1]
    open_mask = (
        (times.dt.date == last_time.date())
        & (times.dt.hour == 13)
        & (times.dt.minute == 30)
    )
    if not open_mask.any():
        return None
    open_idx = times[open_mask].index[0]
    range_bar = df.loc[open_idx]
    range_high, range_low = range_bar["high"], range_bar["low"]

    after = df.loc[open_idx + 1:]
    if len(after) < 3:
        return None

    atr_v = atr(df, 14).iloc[-1]
    entry = df["close"].iloc[-1]

    def find_fvg(direction: str) -> tuple[float, float] | None:
        idxs = list(after.index)
        for j in range(2, len(idxs)):
            c1, c3 = after.loc[idxs[j - 2]], after.loc[idxs[j]]
            if direction == "bull" and c1["high"] < c3["low"]:
                return (c1["high"], c3["low"])
            if direction == "bear" and c1["low"] > c3["high"]:
                return (c3["high"], c1["low"])
        return None

    broke_up = after["high"].max() > range_high
    broke_down = after["low"].min() < range_low

    if broke_up and not broke_down:
        fvg = find_fvg("bull")
        if fvg and fvg[0] <= entry <= fvg[1]:
            stop = range_low - 0.2 * atr_v
            return StrategyResult(
                Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.65,
                ["Opening range breakout up", "Retest into bullish FVG"],
            )
    if broke_down and not broke_up:
        fvg = find_fvg("bear")
        if fvg and fvg[0] <= entry <= fvg[1]:
            stop = range_high + 0.2 * atr_v
            return StrategyResult(
                Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.65,
                ["Opening range breakdown", "Retest into bearish FVG"],
            )
    return None


@register(StrategyName.VOLATILITY_BREAKOUT.value)
def volatility_breakout(df: pd.DataFrame, ctx: pd.DataFrame) -> Optional[StrategyResult]:
    close = df["close"]
    atr_series = atr(df, 14)
    atr_v = atr_series.iloc[-1]
    atr_expansion = atr_v > atr_series.tail(20).mean() * 1.3
    upper, mid, lower = bollinger_bands(close, 20, 2.0)
    bandwidth = (upper - lower) / mid
    squeeze_before = bandwidth.iloc[-5] < bandwidth.tail(30).mean() * 0.8
    entry = close.iloc[-1]
    if atr_expansion and squeeze_before and entry > upper.iloc[-1]:
        stop = entry - 1.3 * atr_v
        return StrategyResult(Direction.LONG, entry, stop, _rr_targets(entry, stop, Direction.LONG), atr_v, 0.6, ["Volatility expansion from squeeze", "Breaks upper band"])
    if atr_expansion and squeeze_before and entry < lower.iloc[-1]:
        stop = entry + 1.3 * atr_v
        return StrategyResult(Direction.SHORT, entry, stop, _rr_targets(entry, stop, Direction.SHORT), atr_v, 0.6, ["Volatility expansion from squeeze", "Breaks lower band"])
    return None
