"""Historical data loading for backtesting.

Supports CSV files (columns: time,open,high,low,close,volume) and, if the
optional `yfinance` package is installed, direct download for symbols that
have a Yahoo Finance ticker equivalent (e.g. XAUUSD -> GC=F, EURUSD -> EURUSD=X).
No look-ahead is introduced here — the loader only returns bars up to "now";
it is the backtester's job to only reveal one bar at a time.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

YAHOO_TICKERS = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCHF": "CHF=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "NAS100": "^NDX",
    "SPX500": "^GSPC",
    "GER40": "^GDAXI",
}


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df.sort_values("time").reset_index(drop=True)


def load_from_yfinance(instrument: str, period: str = "60d", interval: str = "5m") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            "yfinance is not installed. Run `pip install yfinance` or use load_csv() with your own data."
        ) from exc

    ticker = YAHOO_TICKERS.get(instrument)
    if not ticker:
        raise ValueError(f"No Yahoo Finance ticker mapping for {instrument}")

    raw = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if raw.empty:
        raise RuntimeError(f"No data returned for {instrument} ({ticker})")

    raw = raw.reset_index()
    time_col = "Datetime" if "Datetime" in raw.columns else "Date"
    df = pd.DataFrame({
        "time": raw[time_col],
        "open": raw["Open"].astype(float),
        "high": raw["High"].astype(float),
        "low": raw["Low"].astype(float),
        "close": raw["Close"].astype(float),
        "volume": raw["Volume"].astype(float) if "Volume" in raw.columns else 0.0,
    })
    return df.sort_values("time").reset_index(drop=True)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample a finer-grained OHLCV frame to a coarser timeframe, e.g. '1H'."""
    indexed = df.set_index("time")
    out = indexed.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return out.reset_index()
