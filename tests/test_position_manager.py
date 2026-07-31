from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradingbot.models import Direction, Position, TakeProfitLevel
from tradingbot.position_manager import PositionManagementEngine


class _FakeBroker:
    def __init__(self):
        self.closed: list[tuple[str, float]] = []
        self.modified: list[tuple[str, float]] = []

    async def close_position(self, position_id: str, fraction: float = 1.0) -> bool:
        self.closed.append((position_id, fraction))
        return True

    async def modify_position(self, position_id: str, stop_loss=None, take_profit=None) -> bool:
        self.modified.append((position_id, stop_loss))
        return True


def _long_position(entry=1.1000, stop=1.0950, tp=1.1100) -> Position:
    return Position(
        id="p1", instrument="EURUSD", direction=Direction.LONG, entry_price=entry,
        quantity=1000.0, initial_quantity=1000.0, stop_loss=stop, initial_stop_loss=stop,
        take_profit_levels=[TakeProfitLevel(price=tp, close_fraction=1.0)],
        opened_at=datetime.utcnow(),
    )


def _short_position(entry=1.1000, stop=1.1050, tp=1.0900) -> Position:
    return Position(
        id="p2", instrument="EURUSD", direction=Direction.SHORT, entry_price=entry,
        quantity=1000.0, initial_quantity=1000.0, stop_loss=stop, initial_stop_loss=stop,
        take_profit_levels=[TakeProfitLevel(price=tp, close_fraction=1.0)],
        opened_at=datetime.utcnow(),
    )


@pytest.mark.asyncio
async def test_long_position_closes_when_price_hits_stop_loss():
    broker = _FakeBroker()
    engine = PositionManagementEngine(broker)
    pos = _long_position()

    # Price has fallen well past the stop-loss (e.g. a fast-moving market).
    actions = await engine.manage(pos, current_price=1.0800, current_atr=0.001, signal_still_valid=True)

    assert any(a.action == "exit_stop_loss" for a in actions)
    assert broker.closed == [("p1", 1.0)]


@pytest.mark.asyncio
async def test_short_position_closes_when_price_hits_stop_loss():
    broker = _FakeBroker()
    engine = PositionManagementEngine(broker)
    pos = _short_position()

    # Price has risen well past the stop-loss for a short.
    actions = await engine.manage(pos, current_price=1.1200, current_atr=0.001, signal_still_valid=True)

    assert any(a.action == "exit_stop_loss" for a in actions)
    assert broker.closed == [("p2", 1.0)]


@pytest.mark.asyncio
async def test_long_position_closes_when_price_hits_take_profit():
    broker = _FakeBroker()
    engine = PositionManagementEngine(broker)
    pos = _long_position()

    actions = await engine.manage(pos, current_price=1.1150, current_atr=0.001, signal_still_valid=True)

    assert any(a.action == "exit_take_profit" for a in actions)
    assert broker.closed == [("p1", 1.0)]


@pytest.mark.asyncio
async def test_position_within_range_is_not_closed():
    broker = _FakeBroker()
    engine = PositionManagementEngine(broker)
    pos = _long_position()

    # Price is between stop-loss and take-profit, no exit should fire.
    actions = await engine.manage(pos, current_price=1.1010, current_atr=0.001, signal_still_valid=True)

    assert not any(a.action.startswith("exit_stop") or a.action.startswith("exit_take") for a in actions)
    assert broker.closed == []


@pytest.mark.asyncio
async def test_loss_never_exceeds_stop_distance():
    """The core risk promise: a position can never lose more than its
    stop-loss distance, no matter how far price overshoots in one step."""
    broker = _FakeBroker()
    engine = PositionManagementEngine(broker)
    pos = _long_position(entry=1.1000, stop=1.0950)

    # Price crashes far below the stop (simulating a big jump/gap).
    await engine.manage(pos, current_price=1.0500, current_atr=0.001, signal_still_valid=True)

    assert broker.closed == [("p1", 1.0)]
