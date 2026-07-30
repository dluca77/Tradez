"""Entrypoint: runs the Autonomous Trading Controller in paper-trading mode
alongside the read-only dashboard. This is the one-command way to bring the
bot fully online after installation.

    python run_paper.py
"""
from __future__ import annotations

import asyncio
import threading

import uvicorn

from tradingbot.broker.mock import MockBroker
from tradingbot.config import load_config
from tradingbot.controller import AutonomousTradingController
from tradingbot.dashboard.app import create_app
from tradingbot.logging_config import configure_logging


async def main() -> None:
    cfg = load_config()
    configure_logging(cfg.get("logging", "level", default="INFO"), cfg.get("logging", "path", default="logs/tradingbot.log"))

    broker = MockBroker(starting_balance=cfg.starting_balance)
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
    await asyncio.gather(server_task, controller_task)


if __name__ == "__main__":
    asyncio.run(main())
