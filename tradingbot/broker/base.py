"""Abstract broker interface. Concrete brokers (MT5, OANDA, IBKR, Mock) implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from tradingbot.models import Candle, Order, OrderType, Position, Direction


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin_used: float
    margin_available: float
    currency: str = "USD"


@dataclass
class QuoteTick:
    instrument: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class BrokerInterface(ABC):
    """Every broker integration must implement this contract.

    No scraping or unofficial login flows — only official broker APIs.
    """

    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def is_connected(self) -> bool: ...

    @abstractmethod
    async def get_account_info(self) -> AccountInfo: ...

    @abstractmethod
    async def get_open_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_quote(self, instrument: str) -> QuoteTick: ...

    @abstractmethod
    async def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]: ...

    @abstractmethod
    async def place_order(
        self,
        instrument: str,
        direction: Direction,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Order: ...

    @abstractmethod
    async def modify_position(
        self, position_id: str, stop_loss: float | None = None, take_profit: float | None = None
    ) -> bool: ...

    @abstractmethod
    async def close_position(self, position_id: str, fraction: float = 1.0) -> bool: ...
