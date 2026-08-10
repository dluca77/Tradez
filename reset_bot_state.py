"""One-off utility: reset the bot's risk/optimization session state back to
defaults, without touching trade history.

Use this when switching to a genuinely new trading context (e.g. moving
from the mock simulator to a real MT5 demo account) where leftover state
like risk_multiplier or trades_today from the old context no longer means
anything.

Usage:
    python reset_bot_state.py
"""
from __future__ import annotations

from tradingbot.config import load_config
from tradingbot.database import Database


def main() -> None:
    cfg = load_config()
    db = Database(cfg.get("database", "path", default="data/tradingbot.db"))
    with db._connect() as conn:
        conn.execute("DELETE FROM session_state")
        conn.execute("DELETE FROM optimization_state")
    print("session_state and optimization_state cleared. Trade history was left untouched.")
    print("Next bot startup will begin with default risk_multiplier=1.0, min_confidence=75, no cooldown.")


if __name__ == "__main__":
    main()
