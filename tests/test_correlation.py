from __future__ import annotations

from tradingbot.correlation import correlation_penalty
from tradingbot.models import Direction, MarketRegime, Position, StrategyName, TakeProfitLevel


def _position(instrument: str, direction: Direction) -> Position:
    return Position(
        id="x", instrument=instrument, direction=direction, entry_price=1.0, quantity=1.0,
        initial_quantity=1.0, stop_loss=0.99, initial_stop_loss=0.99,
        take_profit_levels=[TakeProfitLevel(price=1.01, close_fraction=1.0)],
        strategy=StrategyName.TREND_FOLLOWING, regime_at_entry=MarketRegime.STRONG_UPTREND,
        confidence=90, risk_amount=25.0,
    )


def test_correlated_positions_reduce_score():
    open_positions = [_position("EURUSD", Direction.LONG)]
    penalty = correlation_penalty(open_positions, Direction.LONG, "GBPUSD")
    assert penalty < 1.0


def test_uncorrelated_direction_no_penalty():
    open_positions = [_position("USDJPY", Direction.LONG)]
    penalty = correlation_penalty(open_positions, Direction.LONG, "EURUSD")
    assert penalty == 1.0


def test_no_open_positions_no_penalty():
    penalty = correlation_penalty([], Direction.LONG, "EURUSD")
    assert penalty == 1.0


def test_same_direction_indices_reduce_score():
    open_positions = [_position("NAS100", Direction.SHORT)]
    penalty = correlation_penalty(open_positions, Direction.SHORT, "SPX500")
    assert penalty < 1.0


def test_opposite_direction_indices_no_penalty():
    open_positions = [_position("NAS100", Direction.LONG)]
    penalty = correlation_penalty(open_positions, Direction.SHORT, "SPX500")
    assert penalty == 1.0


def test_index_vs_non_index_no_penalty():
    open_positions = [_position("NAS100", Direction.SHORT)]
    penalty = correlation_penalty(open_positions, Direction.SHORT, "USDJPY")
    assert penalty == 1.0
