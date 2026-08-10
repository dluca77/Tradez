from __future__ import annotations

import pytest

from tradingbot.broker.factory import create_broker
from tradingbot.broker.mock import MockBroker
from tradingbot.config import load_config


def test_factory_returns_mock_broker_by_default():
    cfg = load_config()
    broker = create_broker(cfg)
    assert isinstance(broker, MockBroker)


def test_factory_raises_for_mt5_without_terminal_available():
    # On a plain Linux host (no MT5 terminal/Wine), the MetaTrader5 package
    # itself is not importable — the adapter must fail with a clear error
    # rather than crash unhelpfully or silently do nothing.
    cfg = load_config()
    cfg.raw["broker"]["name"] = "mt5"
    with pytest.raises(RuntimeError, match="MetaTrader5"):
        create_broker(cfg)


def test_factory_raises_for_unknown_broker():
    cfg = load_config()
    cfg.raw["broker"]["name"] = "does_not_exist"
    with pytest.raises(ValueError):
        create_broker(cfg)


def test_factory_raises_for_oanda_without_credentials(monkeypatch):
    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    cfg = load_config()
    cfg.raw["broker"]["name"] = "oanda"
    with pytest.raises(RuntimeError):
        create_broker(cfg)
