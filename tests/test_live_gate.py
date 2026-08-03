from __future__ import annotations

import pytest

from tradingbot.config import load_config
from tradingbot.database import Database
from tradingbot.live_gate import MIN_PAPER_TRADES, LiveTradingGate


class _FakeAccount:
    balance = 10_000.0


class _FakeBroker:
    def __init__(self, connectable: bool = True):
        self.connectable = connectable

    async def connect(self) -> bool:
        return self.connectable

    async def get_account_info(self):
        return _FakeAccount()


@pytest.mark.asyncio
async def test_gate_blocks_when_live_trading_disabled(tmp_path):
    cfg = load_config()
    assert cfg.live_trading_enabled is False
    db = Database(tmp_path / "test.db")
    gate = LiveTradingGate(cfg, db)
    result = await gate.evaluate(_FakeBroker())
    assert result.approved is False
    assert result.checks["config_live_trading_enabled"] is False


@pytest.mark.asyncio
async def test_gate_blocks_when_broker_unreachable(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "test.db")
    gate = LiveTradingGate(cfg, db)
    result = await gate.evaluate(_FakeBroker(connectable=False))
    assert result.approved is False
    assert result.checks["broker_connection_validated"] is False


@pytest.mark.asyncio
async def test_gate_blocks_with_insufficient_paper_trades(tmp_path):
    cfg = load_config()
    db = Database(tmp_path / "test.db")
    gate = LiveTradingGate(cfg, db)
    result = await gate.evaluate(_FakeBroker())
    assert result.checks["paper_trading_sample_size"] is False
    assert result.approved is False


@pytest.mark.asyncio
async def test_gate_requires_enough_closed_trades_constant():
    assert MIN_PAPER_TRADES >= 30
