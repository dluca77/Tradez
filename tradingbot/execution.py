"""Order Execution Engine: places orders through the broker interface and
confirms the fill actually happened."""
from __future__ import annotations

import structlog

from tradingbot.broker.base import BrokerInterface
from tradingbot.models import Direction, Order, OrderType

log = structlog.get_logger(__name__)


class OrderExecutionEngine:
    def __init__(self, broker: BrokerInterface):
        self.broker = broker

    def choose_order_type(self, spread_pips: float, avg_spread_pips: float) -> OrderType:
        # Market order in normal conditions; if spread is temporarily wide,
        # prefer a limit order at a slightly better price rather than
        # chasing the market.
        if spread_pips > avg_spread_pips * 1.5:
            return OrderType.LIMIT
        return OrderType.MARKET

    async def open_position(
        self,
        instrument: str,
        direction: Direction,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> Order | None:
        if quantity <= 0:
            log.warning("execution.reject_zero_quantity", instrument=instrument)
            return None
        order = await self.broker.place_order(
            instrument=instrument,
            direction=direction,
            quantity=quantity,
            order_type=order_type,
            price=limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if order.status != "filled":
            log.error("execution.not_filled", instrument=instrument, order_id=order.id)
            return None
        log.info(
            "execution.filled",
            instrument=instrument,
            direction=direction.value,
            quantity=quantity,
            price=order.filled_price,
        )
        return order
