"""Entrypoint: runs the Autonomous Trading Controller in paper-trading mode
alongside the read-only dashboard. This is the one-command way to bring the
bot fully online after installation.

    python run_paper.py
"""
from __future__ import annotations

import asyncio

import uvicorn

from tradingbot.broker.factory import create_broker
from tradingbot.config import load_config
from tradingbot.controller import AutonomousTradingController
from tradingbot.dashboard.app import create_app
from tradingbot.logging_config import configure_logging


async def main() -> None:
    cfg = load_config()
    configure_logging(cfg.get("logging", "level", default="INFO"), cfg.get("logging", "path", default="logs/tradingbot.log"))

    # Respect config.yaml's broker.name (mock/mt5/oanda) instead of always
    # using the synthetic MockBroker — this was previously hardcoded here,
    # so setting broker.name: mt5 in config.yaml silently had no effect and
    # every "paper trading" run was actually against random synthetic
    # prices, never real (even demo) market data.
    broker = create_broker(cfg)
    controller = AutonomousTradingController(cfg, broker)

    app = create_app(controller)
    host = cfg.get("dashboard", "host", default="0.0.0.0")
    port = cfg.get("dashboard", "port", default=8080)

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    controller_task = asyncio.create_task(
        controller.run_forever(cfg.get("scanning", "scan_interval_seconds", default=15))
    )

    print(f"Dashboard running at http://{host}:{port}  (mode={cfg.mode}, live_trading_enabled={cfg.live_trading_enabled})")
    print(f"Broker: {cfg.get('broker', 'name', default='mock')}")
    if cfg.get("broker", "name", default="mock") != "mock":
        # This is the one place a real (even demo) account's identity is
        # cheap to double-check before the controller starts firing orders
        # at it every scan cycle — print balance/currency so a wrong
        # account/server in .env is obvious immediately, not after trades
        # have already been placed against it.
        await broker.connect()
        account = await broker.get_account_info()
        print(f"Connected account balance: {account.balance:.2f} {account.currency} — VERIFY this is your intended (demo) account before leaving this running.")
    await asyncio.gather(server_task, controller_task)


if __name__ == "__main__":
    asyncio.run(main())
