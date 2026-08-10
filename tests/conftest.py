from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_trending_df(n: int = 200, start: float = 100.0, drift: float = 0.05, vol: float = 0.3, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = [start]
    for _ in range(n - 1):
        shock = rng.normal(0, vol)
        prices.append(prices[-1] + drift + shock)
    closes = np.array(prices)
    highs = closes + np.abs(rng.normal(0.2, 0.1, n))
    lows = closes - np.abs(rng.normal(0.2, 0.1, n))
    opens = closes + rng.normal(0, 0.05, n)
    volumes = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes})


@pytest.fixture
def trending_up_df() -> pd.DataFrame:
    return make_trending_df(drift=0.15)


@pytest.fixture
def trending_down_df() -> pd.DataFrame:
    return make_trending_df(drift=-0.15)


@pytest.fixture
def sideways_df() -> pd.DataFrame:
    return make_trending_df(drift=0.0, vol=0.3)
