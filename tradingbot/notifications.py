"""Notification Service: informs the user of important events without
requiring them to watch the bot."""
from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)

# Discord embed colors, matching the dashboard's own status palette so the
# two surfaces read as one system (green/red/orange/blue already used for
# pos/neg/warn/lock states there).
_GREEN = 0x3DDC84
_RED = 0xFF6B6B
_ORANGE = 0xF0A93D
_BLUE = 0x6FB3FF
_LEVEL_COLORS = {"error": _RED, "warning": _ORANGE, "info": _BLUE}


class NotificationService:
    def __init__(self, enabled: bool = True, channel: str = "log", webhook_url: str = ""):
        self.enabled = enabled
        self.channel = channel
        self.webhook_url = webhook_url

    def _dispatch(
        self,
        level: str,
        message: str,
        *,
        color: int | None = None,
        title: str | None = None,
        fields: list[tuple[str, str]] | None = None,
    ) -> None:
        if not self.enabled:
            return
        getattr(log, level, log.info)("notification", message=message, channel=self.channel)

        if self.channel in ("discord", "slack") and self.webhook_url:
            self._post_webhook(level, message, color=color, title=title, fields=fields)

    def _post_webhook(
        self,
        level: str,
        message: str,
        *,
        color: int | None = None,
        title: str | None = None,
        fields: list[tuple[str, str]] | None = None,
    ) -> None:
        if self.channel == "discord":
            # Embeds instead of plain content - a colored left border reads
            # info/warning/error at a glance, and structured fields (e.g.
            # the daily summary's pnl/win-rate/trades/profit-factor) are
            # scannable instead of one run-on sentence.
            embed: dict = {"description": message, "color": color if color is not None else _LEVEL_COLORS.get(level, _BLUE)}
            if title:
                embed["title"] = title
            if fields:
                embed["fields"] = [{"name": name, "value": value, "inline": True} for name, value in fields]
            payload = {"embeds": [embed]}
        else:
            prefix = {"error": "\U0001F6A8 ", "warning": "⚠️ ", "info": ""}.get(level, "")
            payload = {"text": f"{prefix}{message}"}
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
        self._dispatch("info", f"{icon} Closed {instrument}: {pnl:+.2f} EUR", color=_GREEN if pnl >= 0 else _RED)

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
            self._dispatch("info", "Geen trades vandaag", title="\U0001F4CA Dagafsluiting")
            return
        pf_label = "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}"
        self._dispatch(
            "info",
            "Resultaat van de afgelopen dag",
            title="\U0001F4CA Dagafsluiting",
            color=_GREEN if pnl >= 0 else _RED,
            fields=[
                ("Winst/verlies", f"{pnl:+.2f} EUR"),
                ("Win rate", f"{win_rate * 100:.0f}%"),
                ("Trades", str(trades)),
                ("Profit factor", pf_label),
            ],
        )

    def weekly_summary(self, pnl: float, win_rate: float, trades: int, profit_factor: float) -> None:
        if trades == 0:
            self._dispatch("info", "Geen trades deze week", title="\U0001F5D3️ Weekafsluiting")
            return
        pf_label = "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}"
        self._dispatch(
            "info",
            "Resultaat van de afgelopen week",
            title="\U0001F5D3️ Weekafsluiting",
            color=_GREEN if pnl >= 0 else _RED,
            fields=[
                ("Winst/verlies", f"{pnl:+.2f} EUR"),
                ("Win rate", f"{win_rate * 100:.0f}%"),
                ("Trades", str(trades)),
                ("Profit factor", pf_label),
            ],
        )
