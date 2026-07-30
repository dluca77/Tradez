"""MetaTrader 5 broker adapter — uses the official `MetaTrader5` Python
package (published by MetaQuotes), which wraps the real MT5 terminal via
its native API. No scraping, no unofficial login flows.

Important platform note: the `MetaTrader5` package only works where an
MT5 terminal can run — natively on Windows, or on Linux/macOS via Wine
with the terminal installed. It cannot be imported on a plain Linux
server without one of those. The import is therefore deferred until
`MT5Broker()` is actually instantiated, so the rest of the bot (including
all tests) works fine in any environment; this adapter simply refuses to
start with a clear error if the terminal isn't available.

Credentials (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER) come only from the
environment (.env) — never hardcoded. The MT5 terminal itself must already
be installed on the host; this package attaches to it, it doesn't install it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tradingbot.broker.base import AccountInfo, BrokerInterface, QuoteTick
from tradingbot.config import env
from tradingbot.models import Candle, Direction, Order, OrderType, Position, TakeProfitLevel

# MT5 symbol names are broker-specific; brokers commonly append a suffix
# (e.g. "EURUSD.m", "XAUUSDm"). Override per-symbol via MT5_SYMBOL_SUFFIX
# in .env if your broker uses one, or extend this map directly.
MT5_SYMBOL_MAP = {
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD",
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "AUDUSD": "AUDUSD", "USDCAD": "USDCAD",
    "NAS100": "NAS100", "SPX500": "SP500", "GER40": "GER40",
}

# tradingbot timeframe string -> MetaTrader5 TIMEFRAME_* constant name
_TIMEFRAME_NAMES = {
    "M1": "TIMEFRAME_M1", "M3": "TIMEFRAME_M3", "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
}


def _import_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The MetaTrader5 package is not available. It requires a running MT5 "
            "terminal (native on Windows, or via Wine elsewhere). Install it with "
            "`pip install MetaTrader5` on a host where an MT5 terminal is installed."
        ) from exc
    return mt5


class MT5Broker(BrokerInterface):
    def __init__(self) -> None:
        self.mt5 = _import_mt5()
        self.login = env("MT5_LOGIN")
        self.password = env("MT5_PASSWORD")
        self.server = env("MT5_SERVER")
        self.suffix = env("MT5_SYMBOL_SUFFIX", "") or ""
        if not self.login or not self.password or not self.server:
            raise RuntimeError(
                "MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER must be set in the environment (.env) "
                "before the MT5 adapter can be used."
            )
        self._connected = False

    def _symbol(self, instrument: str) -> str:
        return MT5_SYMBOL_MAP.get(instrument, instrument) + self.suffix

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def connect(self) -> bool:
        ok = await self._run(self.mt5.initialize, login=int(self.login), password=self.password, server=self.server)
        self._connected = bool(ok)
        return self._connected

    async def is_connected(self) -> bool:
        info = await self._run(self.mt5.terminal_info)
        self._connected = info is not None and getattr(info, "connected", False)
        return self._connected

    async def get_account_info(self) -> AccountInfo:
        info = await self._run(self.mt5.account_info)
        if info is None:
            raise RuntimeError(f"MT5 account_info() failed: {self.mt5.last_error()}")
        return AccountInfo(
            balance=float(info.balance),
            equity=float(info.equity),
            margin_used=float(info.margin),
            margin_available=float(info.margin_free),
            currency=info.currency,
        )

    async def get_open_positions(self) -> list[Position]:
        positions = await self._run(self.mt5.positions_get)
        result: list[Position] = []
        if not positions:
            return result
        reverse_map = {v: k for k, v in MT5_SYMBOL_MAP.items()}
        for p in positions:
            base_symbol = p.symbol[: len(p.symbol) - len(self.suffix)] if self.suffix and p.symbol.endswith(self.suffix) else p.symbol
            instrument = reverse_map.get(base_symbol, base_symbol)
            direction = Direction.LONG if p.type == self.mt5.POSITION_TYPE_BUY else Direction.SHORT
            take_profit = float(p.tp) if p.tp else None
            result.append(Position(
                id=str(p.ticket), instrument=instrument, direction=direction,
                entry_price=float(p.price_open), quantity=float(p.volume), initial_quantity=float(p.volume),
                stop_loss=float(p.sl), initial_stop_loss=float(p.sl),
                take_profit_levels=[TakeProfitLevel(price=take_profit, close_fraction=1.0)] if take_profit else [],
                risk_amount=0.0,
            ))
        return result

    async def get_quote(self, instrument: str) -> QuoteTick:
        symbol = self._symbol(instrument)
        tick = await self._run(self.mt5.symbol_info_tick, symbol)
        if tick is None:
            raise RuntimeError(f"MT5 symbol_info_tick({symbol}) failed: {self.mt5.last_error()}")
        return QuoteTick(instrument=instrument, bid=float(tick.bid), ask=float(tick.ask))

    async def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        symbol = self._symbol(instrument)
        tf_const = getattr(self.mt5, _TIMEFRAME_NAMES.get(timeframe, "TIMEFRAME_M5"))
        rates = await self._run(self.mt5.copy_rates_from_pos, symbol, tf_const, 0, count)
        if rates is None:
            raise RuntimeError(f"MT5 copy_rates_from_pos({symbol}) failed: {self.mt5.last_error()}")
        return [
            Candle(
                time=datetime.fromtimestamp(int(r["time"]), tz=timezone.utc),
                open=float(r["open"]), high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
                volume=float(r["tick_volume"]),
            )
            for r in rates
        ]

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
        symbol = self._symbol(instrument)
        mt5 = self.mt5

        action = mt5.TRADE_ACTION_DEAL if order_type == OrderType.MARKET else mt5.TRADE_ACTION_PENDING
        type_map = {
            (OrderType.MARKET, Direction.LONG): mt5.ORDER_TYPE_BUY,
            (OrderType.MARKET, Direction.SHORT): mt5.ORDER_TYPE_SELL,
            (OrderType.LIMIT, Direction.LONG): mt5.ORDER_TYPE_BUY_LIMIT,
            (OrderType.LIMIT, Direction.SHORT): mt5.ORDER_TYPE_SELL_LIMIT,
            (OrderType.STOP, Direction.LONG): mt5.ORDER_TYPE_BUY_STOP,
            (OrderType.STOP, Direction.SHORT): mt5.ORDER_TYPE_SELL_STOP,
        }
        mt5_type = type_map.get((order_type, direction), mt5.ORDER_TYPE_BUY if direction == Direction.LONG else mt5.ORDER_TYPE_SELL)

        request = {
            "action": action,
            "symbol": symbol,
            "volume": quantity,
            "type": mt5_type,
            "deviation": 20,
            "type_filling": mt5.ORDER_FILLING_IOC,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if price is not None and order_type != OrderType.MARKET:
            request["price"] = price
        if stop_loss is not None:
            request["sl"] = stop_loss
        if take_profit is not None:
            request["tp"] = take_profit

        result = await self._run(mt5.order_send, request)
        filled = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

        return Order(
            id=str(result.order) if result else "",
            instrument=instrument, direction=direction, order_type=order_type, quantity=quantity,
            price=price, stop_loss=stop_loss, take_profit=take_profit,
            status="filled" if filled else "rejected",
            filled_price=float(result.price) if filled else None,
        )

    async def modify_position(
        self, position_id: str, stop_loss: float | None = None, take_profit: float | None = None
    ) -> bool:
        mt5 = self.mt5
        positions = await self._run(mt5.positions_get, ticket=int(position_id))
        if not positions:
            return False
        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "sl": stop_loss if stop_loss is not None else pos.sl,
            "tp": take_profit if take_profit is not None else pos.tp,
        }
        result = await self._run(mt5.order_send, request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    async def close_position(self, position_id: str, fraction: float = 1.0) -> bool:
        mt5 = self.mt5
        positions = await self._run(mt5.positions_get, ticket=int(position_id))
        if not positions:
            return False
        pos = positions[0]
        close_volume = pos.volume if fraction >= 1.0 else round(pos.volume * fraction, 2)
        opposite = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = await self._run(mt5.symbol_info_tick, pos.symbol)
        price = tick.bid if opposite == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": opposite,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = await self._run(mt5.order_send, request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    async def shutdown(self) -> None:
        await self._run(self.mt5.shutdown)
