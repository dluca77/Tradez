"""Mock/paper broker: simulates realistic price action, spreads and order fills
without touching any real account. Used for paper trading and backtesting seed data.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from tradingbot.broker.base import AccountInfo, BrokerInterface, QuoteTick
from tradingbot.models import Candle, Direction, Order, OrderType, Position, TakeProfitLevel

INSTRUMENT_PROFILES = {
    "XAUUSD": dict(price=2400.0, pip=0.1, spread_pips=2.5, ann_vol=0.16),
    "XAGUSD": dict(price=28.5, pip=0.01, spread_pips=3.0, ann_vol=0.24),
    "EURUSD": dict(price=1.085, pip=0.0001, spread_pips=0.6, ann_vol=0.08),
    "GBPUSD": dict(price=1.265, pip=0.0001, spread_pips=0.9, ann_vol=0.09),
    "USDJPY": dict(price=151.2, pip=0.01, spread_pips=0.8, ann_vol=0.09),
    "USDCHF": dict(price=0.905, pip=0.0001, spread_pips=1.0, ann_vol=0.08),
    "AUDUSD": dict(price=0.655, pip=0.0001, spread_pips=1.0, ann_vol=0.10),
    "USDCAD": dict(price=1.365, pip=0.0001, spread_pips=1.0, ann_vol=0.08),
    "NAS100": dict(price=18500.0, pip=1.0, spread_pips=1.5, ann_vol=0.20),
    "SPX500": dict(price=5300.0, pip=0.5, spread_pips=0.6, ann_vol=0.15),
    "GER40": dict(price=18200.0, pip=1.0, spread_pips=1.2, ann_vol=0.16),
    "USOIL": dict(price=78.0, pip=0.01, spread_pips=3.0, ann_vol=0.35),
}


class MockBroker(BrokerInterface):
    def __init__(self, starting_balance: float = 10_000.0, seed: int | None = None):
        self._rng = random.Random(seed)
        self._connected = False
        self._balance = starting_balance
        self._equity = starting_balance
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = {k: v["price"] for k, v in INSTRUMENT_PROFILES.items()}
        self._candle_cache: dict[str, list[Candle]] = {}

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def is_connected(self) -> bool:
        return self._connected

    def _step_price(self, instrument: str) -> float:
        profile = INSTRUMENT_PROFILES[instrument]
        price = self._prices[instrument]
        dt = 1 / (252 * 24 * 60)  # one simulated minute
        vol = profile["ann_vol"]
        drift = 0.0
        shock = self._rng.gauss(0, 1)
        new_price = price * (1 + drift * dt + vol * (dt ** 0.5) * shock)
        self._prices[instrument] = max(new_price, 0.0001)
        return self._prices[instrument]

    async def get_quote(self, instrument: str) -> QuoteTick:
        profile = INSTRUMENT_PROFILES[instrument]
        mid = self._step_price(instrument)
        half_spread = (profile["spread_pips"] * profile["pip"]) / 2
        return QuoteTick(instrument=instrument, bid=mid - half_spread, ask=mid + half_spread)

    async def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        # Builds a synthetic history ENDING at the current live price
        # (self._prices[instrument], which only ever advances via
        # get_quote's small per-call step). Crucially this does NOT persist
        # its own random walk back into self._prices — earlier it did, and
        # since this is called many times per scan cycle (M5 + H1, per
        # instrument, plus once per open position during management), that
        # meant the "market clock" fast-forwarded by days on every call,
        # compounding into wild, unrealistic price swings within seconds of
        # real time and tripping the drawdown kill switch almost instantly.
        tf_minutes = {"M1": 1, "M3": 3, "M5": 5, "M15": 15, "H1": 60, "H4": 240}.get(timeframe, 5)
        profile = INSTRUMENT_PROFILES[instrument]
        end_price = self._prices[instrument]
        candles: list[Candle] = []
        now = datetime.utcnow()
        vol = profile["ann_vol"]
        dt = tf_minutes / (252 * 24 * 60)
        price = end_price
        for i in range(count):
            o = price
            steps = max(1, tf_minutes)
            path = [o]
            for _ in range(steps):
                shock = self._rng.gauss(0, 1)
                path.append(path[-1] * (1 + vol * (dt / steps) ** 0.5 * shock))
            c = path[-1]
            h = max(path)
            l = min(path)
            vol_bar = abs(self._rng.gauss(1000, 300))
            t = now - timedelta(minutes=tf_minutes * (count - i))
            candles.append(Candle(time=t, open=o, high=h, low=l, close=c, volume=vol_bar))
            price = c
        return candles

    async def get_account_info(self) -> AccountInfo:
        floating_pnl = 0.0
        for pos in self._positions.values():
            profile = INSTRUMENT_PROFILES[pos.instrument]
            current = self._prices[pos.instrument]
            direction_mult = 1 if pos.direction == Direction.LONG else -1
            floating_pnl += (current - pos.entry_price) * direction_mult * pos.quantity / profile["pip"] * profile["pip"]
        self._equity = self._balance + floating_pnl
        margin_used = sum(p.quantity * self._prices[p.instrument] * 0.01 for p in self._positions.values())
        return AccountInfo(
            balance=self._balance,
            equity=self._equity,
            margin_used=margin_used,
            margin_available=max(self._equity - margin_used, 0.0),
        )

    async def get_open_positions(self) -> list[Position]:
        return list(self._positions.values())

    async def place_order(
        self,
        instrument: str,
        direction: Direction,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Order:
        quote = await self.get_quote(instrument)
        fill_price = quote.ask if direction == Direction.LONG else quote.bid
        order = Order(
            id=str(uuid.uuid4()),
            instrument=instrument,
            direction=direction,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="filled",
            filled_price=fill_price,
        )
        tp_levels = [TakeProfitLevel(price=take_profit, close_fraction=1.0)] if take_profit else []
        pos = Position(
            id=order.id,
            instrument=instrument,
            direction=direction,
            entry_price=fill_price,
            quantity=quantity,
            initial_quantity=quantity,
            stop_loss=stop_loss or fill_price,
            initial_stop_loss=stop_loss or fill_price,
            take_profit_levels=tp_levels,
        )
        self._positions[pos.id] = pos
        return order

    async def modify_position(
        self, position_id: str, stop_loss: float | None = None, take_profit: float | None = None
    ) -> bool:
        pos = self._positions.get(position_id)
        if not pos:
            return False
        if stop_loss is not None:
            pos.stop_loss = stop_loss
        if take_profit is not None and pos.take_profit_levels:
            pos.take_profit_levels[0].price = take_profit
        return True

    async def close_position(self, position_id: str, fraction: float = 1.0) -> bool:
        pos = self._positions.get(position_id)
        if not pos:
            return False
        quote = await self.get_quote(pos.instrument)
        exit_price = quote.bid if pos.direction == Direction.LONG else quote.ask
        direction_mult = 1 if pos.direction == Direction.LONG else -1
        close_qty = pos.quantity * fraction
        pnl = (exit_price - pos.entry_price) * direction_mult * close_qty
        self._balance += pnl
        pos.quantity -= close_qty
        if pos.quantity <= 1e-9:
            del self._positions[position_id]
        return True
