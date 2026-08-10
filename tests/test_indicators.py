from __future__ import annotations

from tradingbot.indicators import adx, atr, bollinger_bands, ema, macd, rsi, vwap


def test_ema_tracks_price(trending_up_df):
    e = ema(trending_up_df["close"], 20)
    assert e.iloc[-1] > e.iloc[0]


def test_rsi_bounds(trending_up_df):
    r = rsi(trending_up_df["close"])
    assert (r >= 0).all() and (r <= 100).all()


def test_macd_returns_three_series(trending_up_df):
    macd_line, signal, hist = macd(trending_up_df["close"])
    assert len(macd_line) == len(trending_up_df)
    assert len(signal) == len(trending_up_df)
    assert len(hist) == len(trending_up_df)


def test_atr_nonnegative(trending_up_df):
    a = atr(trending_up_df)
    assert (a.dropna() >= 0).all()


def test_bollinger_bands_ordering(trending_up_df):
    upper, mid, lower = bollinger_bands(trending_up_df["close"])
    valid = upper.dropna().index.intersection(lower.dropna().index)
    assert (upper.loc[valid] >= lower.loc[valid]).all()


def test_adx_bounds(trending_up_df):
    a = adx(trending_up_df)
    assert (a >= 0).all()


def test_vwap_within_reasonable_range(trending_up_df):
    v = vwap(trending_up_df)
    assert v.iloc[-1] > 0
