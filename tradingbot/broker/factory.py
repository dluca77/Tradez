"""Broker factory: picks a concrete BrokerInterface implementation from config."""
from __future__ import annotations

from tradingbot.broker.base import BrokerInterface
from tradingbot.broker.mock import MockBroker
from tradingbot.config import AppConfig


def create_broker(cfg: AppConfig) -> BrokerInterface:
    name = cfg.get("broker", "name", default="mock")

    if name == "mock":
        return MockBroker(starting_balance=cfg.starting_balance)

    if name == "oanda":
        from tradingbot.broker.oanda import OandaBroker
        return OandaBroker()

    if name == "mt5":
        from tradingbot.broker.mt5 import MT5Broker
        return MT5Broker()

    if name == "ibkr":
        raise NotImplementedError(
            "Interactive Brokers adapter not implemented yet. Implement "
            "tradingbot/broker/ibkr.py against the official ib_insync/ibapi package "
            "and register it here."
        )

    raise ValueError(f"Unknown broker.name '{name}' in config.yaml")
