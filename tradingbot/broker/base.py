"""Abstract broker interface. Concrete brokers (MT5, OANDA, IBKR, Mock) implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from tradingbot.models import Candle, Direction, Order, OrderType, Position


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

    async def get_closed_position_result(self, position_id: str) -> tuple[float, float] | None:
        """Return (close_price, realized_pnl) for a position that closed at
        the broker WITHOUT this bot initiating the close (e.g. MT5's own
        stop-loss/take-profit execution happens server-side, invisible to
        close_position()). Default: unsupported: subclasses that can query
        deal/trade history should override this. Returning None means the
        caller must fall back to an approximate/zero record."""
        return None

    async def get_risk_conversion_factor(self, instrument: str) -> float:
        """Multiplier applied to the account-currency risk amount before
        position_sizing.calculate_position_size(), correcting for an
        instrument quoted in a currency other than the account currency
        (e.g. a JPY-denominated index CFD on a EUR account) - without this,
        `quantity = risk_amount / stop_distance` silently assumes 1 unit of
        quote-currency price movement equals 1 unit of account currency,
        which is wildly wrong for a ~155-180:1 currency pair like JPY/EUR.
        Confirmed live: JPN225 risk was landing ~180x smaller than intended
        (a few cents instead of tens of euros) purely from this gap.
        Default 1.0 (no adjustment) for brokers without real per-symbol
        tick economics (mock/backtest) - not a live-money path."""
        return 1.0
