"""Technical indicators used across strategies and regime detection.

All functions accept a pandas DataFrame with columns: open, high, low, close, volume
and return either a pandas Series or a scalar (for the latest value helpers).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def bollinger_bands(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = atr(df, period) * period  # de-smoothed approximation baseline
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr.replace(0, np.nan)
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    cum_vol_price = (typical * df["volume"]).cumsum()
    return (cum_vol_price / cum_vol).fillna(typical)


def swing_highs_lows(df: pd.DataFrame, window: int = 3) -> tuple[pd.Series, pd.Series]:
    highs = df["high"]
    lows = df["low"]
    is_swing_high = highs == highs.rolling(window * 2 + 1, center=True).max()
    is_swing_low = lows == lows.rolling(window * 2 + 1, center=True).min()
    return is_swing_high.fillna(False), is_swing_low.fillna(False)


def nearest_support_resistance(df: pd.DataFrame, lookback: int = 100, window: int = 3):
    recent = df.tail(lookback)
    sh, sl = swing_highs_lows(recent, window)
    resistances = recent.loc[sh, "high"].tolist()
    supports = recent.loc[sl, "low"].tolist()
    last_close = df["close"].iloc[-1]
    resistance_above = min([r for r in resistances if r > last_close], default=None)
    support_below = max([s for s in supports if s < last_close], default=None)
    return support_below, resistance_above
