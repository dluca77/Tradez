"""Tests the MT5 adapter's logic (symbol mapping, request construction) using
a fake `MetaTrader5` module injected into sys.modules, since the real package
cannot be installed/run on this Linux test host without a terminal."""
from __future__ import annotations

import sys
import types

import pytest


class _FakeTick:
    bid = 1.1000
    ask = 1.1002


class _FakeAccountInfo:
    balance = 10_000.0
    equity = 10_050.0
    margin = 100.0
    margin_free = 9_950.0
    currency = "USD"


class _FakePosition:
    ticket = 12345
    symbol = "EURUSD"
    type = 0  # POSITION_TYPE_BUY
    price_open = 1.1000
    volume = 0.5
    sl = 1.0950
    tp = 1.1100


def _make_fake_mt5():
    fake = types.SimpleNamespace()
    fake.POSITION_TYPE_BUY = 0
    fake.POSITION_TYPE_SELL = 1
    fake.ORDER_TYPE_BUY = 0
    fake.ORDER_TYPE_SELL = 1
    fake.ORDER_TYPE_BUY_LIMIT = 2
    fake.ORDER_TYPE_SELL_LIMIT = 3
    fake.ORDER_TYPE_BUY_STOP = 4
    fake.ORDER_TYPE_SELL_STOP = 5
    fake.TRADE_ACTION_DEAL = 1
    fake.TRADE_ACTION_PENDING = 5
    fake.TRADE_ACTION_SLTP = 6
    fake.ORDER_FILLING_IOC = 1
    fake.ORDER_TIME_GTC = 0
    fake.TRADE_RETCODE_DONE = 10009
    fake.TIMEFRAME_M1 = 1
    fake.TIMEFRAME_M3 = 3
    fake.TIMEFRAME_M5 = 5
    fake.TIMEFRAME_M15 = 15
    fake.TIMEFRAME_H1 = 16385
    fake.TIMEFRAME_H4 = 16388

    fake.initialize = lambda **kwargs: True
    fake.terminal_info = lambda: types.SimpleNamespace(connected=True)
    fake.account_info = lambda: _FakeAccountInfo()
    fake.positions_get = lambda **kwargs: (_FakePosition(),)
    fake.symbol_info_tick = lambda symbol: _FakeTick()
    fake.symbol_info = lambda symbol: types.SimpleNamespace(visible=True)
    fake.symbol_select = lambda symbol, enable: True
    fake.last_error = lambda: "no error"

    def fake_order_send(request):
        return types.SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE, order=999, price=1.1001)

    fake.order_send = fake_order_send
    fake.shutdown = lambda: None
    return fake


@pytest.fixture
def fake_mt5_module(monkeypatch):
    fake = _make_fake_mt5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.setenv("MT5_LOGIN", "12345")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Broker-Demo")
    monkeypatch.delenv("MT5_SYMBOL_SUFFIX", raising=False)
    yield fake
    sys.modules.pop("tradingbot.broker.mt5", None)


def test_mt5_broker_constructs_with_fake_module(fake_mt5_module):
    from tradingbot.broker.mt5 import MT5Broker
    broker = MT5Broker()
    assert broker.login == "12345"


def test_mt5_broker_requires_credentials(fake_mt5_module, monkeypatch):
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    from tradingbot.broker.mt5 import MT5Broker
    with pytest.raises(RuntimeError, match="MT5_LOGIN"):
        MT5Broker()


@pytest.mark.asyncio
async def test_mt5_broker_connect_and_account_info(fake_mt5_module):
    from tradingbot.broker.mt5 import MT5Broker
    broker = MT5Broker()
    connected = await broker.connect()
    assert connected is True
    account = await broker.get_account_info()
    assert account.balance == 10_000.0
    assert account.equity == 10_050.0


@pytest.mark.asyncio
async def test_mt5_broker_get_quote(fake_mt5_module):
    from tradingbot.broker.mt5 import MT5Broker
    broker = MT5Broker()
    quote = await broker.get_quote("EURUSD")
    assert quote.bid == 1.1000
    assert quote.ask == 1.1002


@pytest.mark.asyncio
async def test_mt5_broker_open_positions_maps_direction(fake_mt5_module):
    from tradingbot.broker.mt5 import MT5Broker
    from tradingbot.models import Direction
    broker = MT5Broker()
    positions = await broker.get_open_positions()
    assert len(positions) == 1
    assert positions[0].direction == Direction.LONG
    assert positions[0].instrument == "EURUSD"


@pytest.mark.asyncio
async def test_mt5_broker_place_order_returns_filled(fake_mt5_module):
    from tradingbot.broker.mt5 import MT5Broker
    from tradingbot.models import Direction, OrderType
    broker = MT5Broker()
    order = await broker.place_order(
        instrument="EURUSD", direction=Direction.LONG, quantity=0.5,
        order_type=OrderType.MARKET, stop_loss=1.0950, take_profit=1.1100,
    )
    assert order.status == "filled"
    assert order.filled_price == 1.1001


def test_mt5_symbol_suffix_applied(fake_mt5_module, monkeypatch):
    monkeypatch.setenv("MT5_SYMBOL_SUFFIX", ".m")
    from tradingbot.broker.mt5 import MT5Broker
    broker = MT5Broker()
    assert broker._symbol("EURUSD") == "EURUSD.m"
