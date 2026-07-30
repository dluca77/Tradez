"""Entrypoint for LIVE trading.

This script will refuse to place a single real order unless:
  1. config.yaml has live_trading_enabled: true
  2. broker credentials in .env validate against the real broker
  3. paper trading has produced enough sample trades with acceptable
     win rate and profit factor (see tradingbot/live_gate.py)
  4. risk limits in config.yaml are sane and within the hard safety ceiling
  5. the operator explicitly confirms activation at the terminal prompt below

Nothing here bypasses tradingbot.risk_manager's hard limits or the
no-martingale guarantee — this script only decides whether the bot is
ALLOWED to start against a real broker, using the exact same
AutonomousTradingController used in paper trading.
"""
from __future__ import annotations

import asyncio
import sys

import uvicorn

from tradingbot.broker.factory import create_broker
from tradingbot.config import load_config
from tradingbot.controller import AutonomousTradingController
from tradingbot.dashboard.app import create_app
from tradingbot.database import Database
from tradingbot.live_gate import LiveTradingGate
from tradingbot.logging_config import configure_logging


async def main() -> None:
    cfg = load_config()
    configure_logging(cfg.get("logging", "level", default="INFO"), cfg.get("logging", "path", default="logs/tradingbot.log"))

    db = Database(cfg.get("database", "path", default="data/tradingbot.db"))
    broker = create_broker(cfg)

    gate = LiveTradingGate(cfg, db)
    result = await gate.evaluate(broker)

    print("=== Live Trading Activation Gate ===")
    for check, passed in result.checks.items():
        print(f"  [{'OK' if passed else 'FAIL'}] {check}")

    if not result.approved:
        print("\nLive trading NOT approved. Reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")
        print("\nRun paper trading (run_paper.py) longer, fix config.yaml, or validate broker credentials, then retry.")
        sys.exit(1)

    print("\nAll checks passed. This will place REAL orders with REAL money.")
    confirmation = input("Type EXACTLY 'I ACTIVATE LIVE TRADING' to proceed: ")
    if confirmation.strip() != "I ACTIVATE LIVE TRADING":
        print("Confirmation not received. Aborting.")
        sys.exit(1)

    controller = AutonomousTradingController(cfg, broker, db=db)
    app = create_app(controller)
    host = cfg.get("dashboard", "host", default="0.0.0.0")
    port = cfg.get("dashboard", "port", default=8080)

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    print(f"LIVE TRADING ACTIVE. Dashboard at http://{host}:{port}")

    await asyncio.gather(
        server.serve(),
        controller.run_forever(cfg.get("scanning", "scan_interval_seconds", default=15)),
    )


if __name__ == "__main__":
    asyncio.run(main())
