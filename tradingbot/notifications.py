"""Notification Service: informs the user of important events without
requiring them to watch the bot."""
from __future__ import annotations

import httpx
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

        if self.channel in ("discord", "slack") and self.webhook_url:
            self._post_webhook(level, message)

    def _post_webhook(self, level: str, message: str) -> None:
        prefix = {"error": "\U0001F6A8 ", "warning": "⚠️ ", "info": ""}.get(level, "")
        text = f"{prefix}{message}"
        # Discord and Slack incoming webhooks expect different JSON shapes
        # ({"content": ...} vs {"text": ...}) but both accept a plain POST
        # with no auth beyond the URL itself, so one code path covers both.
        payload = {"content": text} if self.channel == "discord" else {"text": text}
        try:
            httpx.post(self.webhook_url, json=payload, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            # A failed notification must never take down the trading loop —
            # log it locally and move on, the trade itself already happened.
            log.warning("notification.webhook_failed", channel=self.channel, detail=str(exc))

    def trade_opened(self, instrument: str, direction: str, confidence: float) -> None:
        self._dispatch("info", f"Opened {direction} {instrument} (confidence {confidence:.0f})")

    def trade_closed(self, instrument: str, pnl: float, r_multiple: float) -> None:
        icon = "✅" if pnl >= 0 else "\U0001F53B"  # green checkmark / red down-triangle
        self._dispatch("info", f"{icon} Closed {instrument}: {pnl:+.2f} EUR")

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

    def daily_summary(self, pnl: float, win_rate: float, trades: int, profit_factor: float) -> None:
        if trades == 0:
            self._dispatch("info", "Dagafsluiting: geen trades vandaag")
            return
        icon = "✅" if pnl >= 0 else "\U0001F53B"
        pf_label = "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}"
        self._dispatch(
            "info",
            f"{icon} Dagafsluiting: {pnl:+.2f} EUR over {trades} trades "
            f"(win rate {win_rate * 100:.0f}%, profit factor {pf_label})",
        )
