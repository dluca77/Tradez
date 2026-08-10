"""OANDA broker adapter — uses OANDA's official v20 REST API only.

Credentials come exclusively from environment variables (OANDA_API_KEY,
OANDA_ACCOUNT_ID, OANDA_ENV) — never hardcoded. This adapter is dormant
until explicitly wired up: `config.yaml` must set `broker.name: oanda` and
`live_trading_enabled: true`, and `LiveTradingGate` (see
`tradingbot/live_gate.py`) must pass, before `run_live.py` will use it.

Instrument codes are translated between this project's convention
(EURUSD, XAUUSD, ...) and OANDA's (EUR_USD, XAU_USD, ...).
"""
from __future__ import annotations

import uuid
from datetime import datetime

import httpx

from tradingbot.broker.base import AccountInfo, BrokerInterface, QuoteTick
from tradingbot.config import env
from tradingbot.models import Candle, Direction, Order, OrderType, Position, TakeProfitLevel

OANDA_INSTRUMENT_MAP = {
    "XAUUSD": "XAU_USD", "XAGUSD": "XAG_USD",
    "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD", "USDJPY": "USD_JPY",
    "USDCHF": "USD_CHF", "AUDUSD": "AUD_USD", "USDCAD": "USD_CAD",
    "NAS100": "NAS100_USD", "SPX500": "SPX500_USD", "GER40": "DE30_EUR",
    "USOIL": "WTICO_USD",
}

OANDA_GRANULARITY = {"M1": "M1", "M3": "M3", "M5": "M5", "M15": "M15", "H1": "H1", "H4": "H4"}

_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaBroker(BrokerInterface):
    def __init__(self) -> None:
        self.api_key = env("OANDA_API_KEY")
        self.account_id = env("OANDA_ACCOUNT_ID")
        self.environment = env("OANDA_ENV", "practice") or "practice"
        if not self.api_key or not self.account_id:
            raise RuntimeError(
                "OANDA_API_KEY and OANDA_ACCOUNT_ID must be set in the environment (.env) "
                "before the OANDA adapter can be used."
            )
        self.base_url = _BASE_URLS.get(self.environment, _BASE_URLS["practice"])
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=15.0,
        )
        self._connected = False

    @staticmethod
    def _to_oanda(instrument: str) -> str:
        return OANDA_INSTRUMENT_MAP.get(instrument, instrument)

    async def connect(self) -> bool:
        try:
            resp = await self._client.get(f"/v3/accounts/{self.account_id}")
            resp.raise_for_status()
            self._connected = True
        except httpx.HTTPError:
            self._connected = False
        return self._connected

    async def is_connected(self) -> bool:
        return self._connected

    async def get_account_info(self) -> AccountInfo:
        resp = await self._client.get(f"/v3/accounts/{self.account_id}/summary")
        resp.raise_for_status()
        acc = resp.json()["account"]
        return AccountInfo(
            balance=float(acc["balance"]),
            equity=float(acc["NAV"]),
            margin_used=float(acc["marginUsed"]),
            margin_available=float(acc["marginAvailable"]),
            currency=acc.get("currency", "USD"),
        )

    async def get_open_positions(self) -> list[Position]:
        resp = await self._client.get(f"/v3/accounts/{self.account_id}/openTrades")
        resp.raise_for_status()
        trades = resp.json().get("trades", [])
        positions: list[Position] = []
        reverse_map = {v: k for k, v in OANDA_INSTRUMENT_MAP.items()}
        for t in trades:
            instrument = reverse_map.get(t["instrument"], t["instrument"])
            units = float(t["currentUnits"])
            direction = Direction.LONG if units > 0 else Direction.SHORT
            stop_loss = float(t["stopLossOrder"]["price"]) if "stopLossOrder" in t else 0.0
            take_profit = float(t["takeProfitOrder"]["price"]) if "takeProfitOrder" in t else None
            positions.append(Position(
                id=t["id"], instrument=instrument, direction=direction,
                entry_price=float(t["price"]), quantity=abs(units), initial_quantity=abs(units),
                stop_loss=stop_loss, initial_stop_loss=stop_loss,
                take_profit_levels=[TakeProfitLevel(price=take_profit, close_fraction=1.0)] if take_profit else [],
                risk_amount=0.0,
            ))
        return positions

    async def get_quote(self, instrument: str) -> QuoteTick:
        oanda_instrument = self._to_oanda(instrument)
        resp = await self._client.get(
            f"/v3/accounts/{self.account_id}/pricing", params={"instruments": oanda_instrument}
        )
        resp.raise_for_status()
        price = resp.json()["prices"][0]
        bid = float(price["bids"][0]["price"])
        ask = float(price["asks"][0]["price"])
        return QuoteTick(instrument=instrument, bid=bid, ask=ask)

    async def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        oanda_instrument = self._to_oanda(instrument)
        granularity = OANDA_GRANULARITY.get(timeframe, "M5")
        resp = await self._client.get(
            f"/v3/instruments/{oanda_instrument}/candles",
            params={"granularity": granularity, "count": count, "price": "M"},
        )
        resp.raise_for_status()
        candles = []
        for c in resp.json().get("candles", []):
            mid = c["mid"]
            candles.append(Candle(
                time=datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
                open=float(mid["o"]), high=float(mid["h"]), low=float(mid["l"]), close=float(mid["c"]),
                volume=float(c.get("volume", 0)),
            ))
        return candles

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
        oanda_instrument = self._to_oanda(instrument)
        units = quantity if direction == Direction.LONG else -quantity

        order_body: dict = {
            "type": "MARKET" if order_type == OrderType.MARKET else order_type.value.upper(),
            "instrument": oanda_instrument,
            "units": str(units),
            "timeInForce": "FOK" if order_type == OrderType.MARKET else "GTC",
            "positionFill": "DEFAULT",
        }
        if order_type != OrderType.MARKET and price is not None:
            order_body["price"] = str(price)
        if stop_loss is not None:
            order_body["stopLossOnFill"] = {"price": str(stop_loss)}
        if take_profit is not None:
            order_body["takeProfitOnFill"] = {"price": str(take_profit)}

        resp = await self._client.post(
            f"/v3/accounts/{self.account_id}/orders", json={"order": order_body}
        )
        resp.raise_for_status()
        body = resp.json()

        fill = body.get("orderFillTransaction")
        order_id = fill["id"] if fill else body.get("orderCreateTransaction", {}).get("id", str(uuid.uuid4()))
        status = "filled" if fill else "pending"
        filled_price = float(fill["price"]) if fill else None

        return Order(
            id=order_id, instrument=instrument, direction=direction, order_type=order_type,
            quantity=quantity, price=price, stop_loss=stop_loss, take_profit=take_profit,
            status=status, filled_price=filled_price,
        )

    async def modify_position(
        self, position_id: str, stop_loss: float | None = None, take_profit: float | None = None
    ) -> bool:
        body: dict = {}
        if stop_loss is not None:
            body["stopLoss"] = {"price": str(stop_loss)}
        if take_profit is not None:
            body["takeProfit"] = {"price": str(take_profit)}
        if not body:
            return True
        resp = await self._client.put(
            f"/v3/accounts/{self.account_id}/trades/{position_id}/orders", json=body
        )
        return resp.status_code < 400

    async def close_position(self, position_id: str, fraction: float = 1.0) -> bool:
        body = {"units": "ALL"} if fraction >= 1.0 else {"units": str(fraction)}
        resp = await self._client.put(
            f"/v3/accounts/{self.account_id}/trades/{position_id}/close", json=body
        )
        return resp.status_code < 400

    async def aclose(self) -> None:
        await self._client.aclose()
