from __future__ import annotations

import pandas as pd
import pytest

from tradingbot.data_loader import load_csv, resample


def test_load_csv_roundtrip(tmp_path):
    df = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=10, freq="min"),
        "open": range(10), "high": range(1, 11), "low": range(0, 10), "close": range(10),
    })
    path = tmp_path / "sample.csv"
    df.to_csv(path, index=False)
    loaded = load_csv(path)
    assert len(loaded) == 10
    assert "volume" in loaded.columns


def test_load_csv_missing_columns_raises(tmp_path):
    df = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=3, freq="min"), "open": [1, 2, 3]})
    path = tmp_path / "bad.csv"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError):
        load_csv(path)


def test_resample_aggregates_correctly():
    df = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=60, freq="min"),
        "open": [1.0] * 60, "high": [2.0] * 60, "low": [0.5] * 60, "close": [1.5] * 60, "volume": [10.0] * 60,
    })
    hourly = resample(df, "1h")
    assert len(hourly) == 1
    assert hourly["high"].iloc[0] == 2.0
    assert hourly["low"].iloc[0] == 0.5
    assert hourly["volume"].iloc[0] == 600.0
