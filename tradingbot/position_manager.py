"""Position Management Engine: manages every open position continuously.

Handles break-even moves, partial take-profits, ATR/technical trailing
stops, time-based exits, and invalidation exits. A stop-loss is NEVER moved
further from entry than its current value — only tightened.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from tradingbot.broker.base import BrokerInterface
from tradingbot.models import Direction, Position

log = structlog.get_logger(__name__)


@dataclass
class ManagementAction:
    action: str
    detail: str


def _r_multiple(pos: Position, current_price: float) -> float:
    risk_per_unit = abs(pos.entry_price - pos.initial_stop_loss)
    if risk_per_unit <= 0:
        return 0.0
    direction_mult = 1 if pos.direction == Direction.LONG else -1
    return (current_price - pos.entry_price) * direction_mult / risk_per_unit


def _tighten_only(pos: Position, new_stop: float) -> float:
    """Never move stop-loss further away from entry (no giving losers more room)."""
    if pos.direction == Direction.LONG:
        return max(pos.stop_loss, new_stop)
    return min(pos.stop_loss, new_stop)


class PositionManagementEngine:
    def __init__(self, broker: BrokerInterface, max_hold: timedelta = timedelta(hours=6)):
        self.broker = broker
        self.max_hold = max_hold

    async def manage(self, pos: Position, current_price: float, current_atr: float, signal_still_valid: bool) -> list[ManagementAction]:
        actions: list[ManagementAction] = []

        # 0) Stop-loss / take-profit hit — this MUST be checked first, before
        # any other management, and must actually close the position. Without
        # this, a stored stop-loss/take-profit is purely decorative: nothing
        # else in this engine enforces it, so a losing trade could otherwise
        # run far past its intended risk.
        direction_mult = 1 if pos.direction == Direction.LONG else -1
        stop_hit = (
            current_price <= pos.stop_loss if pos.direction == Direction.LONG
            else current_price >= pos.stop_loss
        )
        if stop_hit:
            # The caller only records a trade as closed (and notifies the
            # user) when an "exit" action comes back - if the broker itself
            # rejected the close, the position is still really open and
            # reporting it as closed would silently stop managing a real,
            # still-at-risk position while telling the user it's flat.
            if await self.broker.close_position(pos.id, fraction=1.0):
                actions.append(ManagementAction("exit_stop_loss", f"stop-loss hit at {current_price:.5f}"))
            else:
                log.error("position.close_failed", position_id=pos.id, reason="stop_loss")
            return actions

        if pos.take_profit_levels:
            final_tp = pos.take_profit_levels[-1].price
            tp_hit = (
                current_price >= final_tp if pos.direction == Direction.LONG
                else current_price <= final_tp
            )
            if tp_hit:
                if await self.broker.close_position(pos.id, fraction=1.0):
                    actions.append(ManagementAction("exit_take_profit", f"take-profit hit at {current_price:.5f}"))
                else:
                    log.error("position.close_failed", position_id=pos.id, reason="take_profit")
                return actions

        r = _r_multiple(pos, current_price)

        # 1) Break-even at 1R
        if r >= 1.0 and not pos.breakeven_moved:
            new_stop = _tighten_only(pos, pos.entry_price)
            if new_stop != pos.stop_loss:
                await self.broker.modify_position(pos.id, stop_loss=new_stop)
                pos.stop_loss = new_stop
                pos.breakeven_moved = True
                actions.append(ManagementAction("breakeven", f"stop moved to entry at {r:.2f}R"))

        # 2) Partial profit taking at 1.5R and 2R. Bank the realized pnl of
        # each partial onto the position so the final close (which only
        # ever sees the REMAINING quantity) doesn't lose track of profit
        # already locked in — without this, a trade that took real profit
        # at 1.5R/2R and then trailed back down before its final exit
        # recorded as a small win or even a loss, because only the last
        # leg's price move was ever counted.
        if r >= 1.5 and pos.quantity > pos.initial_quantity * 0.55:
            close_qty = pos.quantity * (0.3 / (pos.quantity / pos.initial_quantity))
            if await self.broker.close_position(pos.id, fraction=0.3 / (pos.quantity / pos.initial_quantity)):
                pos.realized_pnl += (current_price - pos.entry_price) * direction_mult * close_qty
                pos.quantity *= 0.7
                actions.append(ManagementAction("partial_close", f"closed 30% at {r:.2f}R"))
            else:
                log.error("position.close_failed", position_id=pos.id, reason="partial_1.5R")
        elif r >= 2.0 and pos.quantity > pos.initial_quantity * 0.25:
            close_qty = pos.quantity * (0.3 / (pos.quantity / pos.initial_quantity))
            if await self.broker.close_position(pos.id, fraction=0.3 / (pos.quantity / pos.initial_quantity)):
                pos.realized_pnl += (current_price - pos.entry_price) * direction_mult * close_qty
                pos.quantity *= 0.7
                actions.append(ManagementAction("partial_close", f"closed additional 30% at {r:.2f}R"))
            else:
                log.error("position.close_failed", position_id=pos.id, reason="partial_2.0R")

        # 3) ATR trailing stop once in profit beyond 1R
        if r >= 1.0:
            direction_mult = 1 if pos.direction == Direction.LONG else -1
            trail_price = current_price - direction_mult * 1.2 * current_atr
            new_stop = _tighten_only(pos, trail_price)
            if new_stop != pos.stop_loss:
                await self.broker.modify_position(pos.id, stop_loss=new_stop)
                pos.stop_loss = new_stop
                pos.trailing_active = True
                actions.append(ManagementAction("trailing_stop", f"trailed to {new_stop:.5f}"))

        # 3b) Time-decaying profit lock ("ROI table", concept borrowed from
        # freqtrade's minimum_roi): waiting the full max_hold for the
        # strategy's original target risks giving back real, already-earned
        # profit if price reverses before getting there. The R bar needed
        # to lock in a full exit drops as more of max_hold elapses, so a
        # trade sitting on decent-but-not-ideal profit late in its life
        # gets banked instead of gambled on reaching the original target
        # (or worse, riding the max-hold time exit down to a wash/loss).
        if self.max_hold.total_seconds() > 0:
            elapsed_frac = (datetime.utcnow() - pos.opened_at) / self.max_hold
            roi_bar = None
            if elapsed_frac >= 0.75:
                roi_bar = 0.3
            elif elapsed_frac >= 0.5:
                roi_bar = 1.0
            elif elapsed_frac >= 0.25:
                roi_bar = 1.5
            if roi_bar is not None and r >= roi_bar:
                if await self.broker.close_position(pos.id, fraction=1.0):
                    actions.append(ManagementAction(
                        "exit_roi_decay",
                        f"locked in {r:.2f}R at {elapsed_frac:.0%} of max hold (bar was {roi_bar}R)",
                    ))
                    return actions
                else:
                    log.error("position.close_failed", position_id=pos.id, reason="roi_decay")

        # 4) Signal invalidated -> exit
        if not signal_still_valid:
            if await self.broker.close_position(pos.id, fraction=1.0):
                actions.append(ManagementAction("exit_invalidated", "original signal no longer valid"))
            else:
                log.error("position.close_failed", position_id=pos.id, reason="invalidated")
            return actions

        # 5) Time-based exit
        if datetime.utcnow() - pos.opened_at > self.max_hold:
            if not await self.broker.close_position(pos.id, fraction=1.0):
                log.error("position.close_failed", position_id=pos.id, reason="max_hold")
                return actions
            actions.append(ManagementAction("exit_time", "max hold time exceeded"))

        return actions

    async def emergency_close_all(self, positions: list[Position], reason: str) -> None:
        for pos in positions:
            await self.broker.close_position(pos.id, fraction=1.0)
            log.warning("position.emergency_close", instrument=pos.instrument, reason=reason)
