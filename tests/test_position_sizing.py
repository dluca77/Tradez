from __future__ import annotations

from tradingbot.position_sizing import calculate_position_size


def test_position_size_scales_with_risk():
    small = calculate_position_size("EURUSD", 10_000, 0.001, 1.1000, 1.0950)
    large = calculate_position_size("EURUSD", 10_000, 0.005, 1.1000, 1.0950)
    assert large.lots_or_units > small.lots_or_units


def test_position_size_respects_risk_amount():
    result = calculate_position_size("EURUSD", 10_000, 0.0025, 1.1000, 1.0950)
    assert abs(result.risk_amount - 25.0) < 1e-6
    assert abs(result.lots_or_units * result.stop_distance - result.risk_amount) < 1e-6


def test_zero_stop_distance_returns_zero():
    result = calculate_position_size("EURUSD", 10_000, 0.0025, 1.1000, 1.1000)
    assert result.lots_or_units == 0.0
