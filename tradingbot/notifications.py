"""Notification Service: informs the user of important events without
requiring them to watch the bot."""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class NotificationService:
    def __init__(self, enabled: bool = True, channel: str = "log", webhook_url: str = ""):
        self.enabled = enabled
        self.channel = channel
        self.webhook_url = webhook_url

    def _dispatch(self, level: str, message: str) -> None:
        if not self.enabled:
            return
        getattr(log, level, log.info)("notification", message=message, channel=self.channel)
        # channel == "webhook" would POST to self.webhook_url via httpx here.

    def trade_opened(self, instrument: str, direction: str, confidence: float) -> None:
        self._dispatch("info", f"Opened {direction} {instrument} (confidence {confidence:.0f})")

    def trade_closed(self, instrument: str, pnl: float, r_multiple: float) -> None:
        level = "info" if pnl >= 0 else "warning"
        self._dispatch(level, f"Closed {instrument}: PnL {pnl:+.2f} ({r_multiple:+.2f}R)")

    def large_move(self, instrument: str, pnl: float) -> None:
        self._dispatch("warning", f"Large move on {instrument}: {pnl:+.2f}")

    def safety_stop(self, reason: str) -> None:
        self._dispatch("error", f"Safety stop triggered: {reason}")

    def broker_issue(self, detail: str) -> None:
        self._dispatch("error", f"Broker issue: {detail}")

    def risk_limit_hit(self, limit: str) -> None:
        self._dispatch("warning", f"Risk limit reached: {limit}")

    def bot_paused(self, reason: str) -> None:
        self._dispatch("warning", f"Bot paused: {reason}")

    def unexpected_error(self, detail: str) -> None:
        self._dispatch("error", f"Unexpected error: {detail}")
