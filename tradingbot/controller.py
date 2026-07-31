"""Autonomous Trading Controller: runs the full 20-step autonomous cycle
continuously. This is the single entry point that ties every module
together for paper trading (and, later, live trading with the same code
path against a real broker implementation).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import structlog

from tradingbot.broker.base import BrokerInterface
from tradingbot.broker.mock import INSTRUMENT_PROFILES
from tradingbot.config import AppConfig
from tradingbot.correlation import combined_usd_exposure, correlation_penalty
from tradingbot.costs import estimate_costs
from tradingbot.database import Database
from tradingbot.execution import OrderExecutionEngine
from tradingbot.models import Direction, OrderType
from tradingbot.news_filter import INSTRUMENT_CURRENCIES, NewsFilter
from tradingbot.notifications import NotificationService
from tradingbot.optimization import SelfOptimizationModule
from tradingbot.performance import compute_performance, historical_winrates
from tradingbot.position_manager import PositionManagementEngine
from tradingbot.position_sizing import calculate_position_size
from tradingbot.recovery import RecoveryService
from tradingbot.risk_manager import DynamicRiskManager, SessionState
from tradingbot.safety import SafetyModule
from tradingbot.scanner import scan_markets

log = structlog.get_logger(__name__)


class AutonomousTradingController:
    def __init__(self, cfg: AppConfig, broker: BrokerInterface, db: Database | None = None):
        self.cfg = cfg
        self.broker = broker
        self.db = db or Database(cfg.get("database", "path", default="data/tradingbot.db"))
        self.risk_manager = DynamicRiskManager(cfg.risk)
        self.execution = OrderExecutionEngine(broker)
        # Shortened from the 6h production default to 1h during this paper-
        # trading test phase, purely so trades cycle through faster while
        # building the 30-trade sample needed for the live-trading gate.
        self.position_manager = PositionManagementEngine(broker, max_hold=timedelta(hours=1))
        self.safety = SafetyModule(self.db, cfg.risk.max_monthly_drawdown_pct)
        self.recovery = RecoveryService(broker, self.db)
        self.notifications = NotificationService(
            enabled=cfg.get("notifications", "enabled", default=True),
            channel=cfg.get("notifications", "channel", default="log"),
        )
        self.optimizer = SelfOptimizationModule(self.db, cfg.get("optimization", "min_sample_size", default=30))
        self.news_filter = NewsFilter(
            pre_minutes=cfg.get("news", "pre_event_blackout_minutes", default=30),
            post_minutes=cfg.get("news", "post_event_blackout_minutes", default=15),
            extreme_minutes=cfg.get("news", "high_impact_blackout_minutes", default=60),
        )

        self.state = SessionState(
            day_start_equity=cfg.starting_balance,
            week_start_equity=cfg.starting_balance,
            month_start_equity=cfg.starting_balance,
            peak_equity=cfg.starting_balance,
            day_peak_equity=cfg.starting_balance,
        )
        self._positions_meta: dict[str, dict] = {}  # position_id -> signal metadata
        self.running = False
        self.paused = False
        self._day_date = datetime.utcnow().date()

    async def startup(self) -> None:
        await self.recovery.recover()
        self.safety.mark_data_received()

        # The session baselines must reflect the REAL broker's actual
        # starting equity, not cfg.starting_balance (a mock/papertrading
        # assumption of 10000). Against a real account with a different
        # balance (e.g. a 100000 MT5 demo account), comparing live equity
        # to the config's 10000 baseline produced a bogus "900% daily
        # profit" reading that immediately tripped the profit-lock and
        # blocked every trade from the first cycle onward.
        try:
            account = await self.broker.get_account_info()
            self.state.day_start_equity = account.equity
            self.state.week_start_equity = account.equity
            self.state.month_start_equity = account.equity
            self.state.peak_equity = account.equity
            self.state.day_peak_equity = account.equity
        except Exception as exc:  # noqa: BLE001
            log.error("controller.startup_equity_fetch_failed", error=str(exc))

        log.info("controller.started", mode=self.cfg.mode)

    async def run_forever(self, interval_seconds: int = 15) -> None:
        self.running = True
        await self.startup()
        while self.running:
            try:
                await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                log.error("controller.cycle_error", error=str(exc))
                self.notifications.unexpected_error(str(exc))
            await asyncio.sleep(interval_seconds)

    def stop(self) -> None:
        self.running = False

    async def run_cycle(self) -> None:
        # Roll the hour/day trade counters. Without this, trades_this_hour
        # and trades_today only ever climb until they permanently exceed
        # max_trades_per_hour/max_trades_per_day, which blocks every future
        # trade for the rest of the run (observed: bot went completely
        # silent, no open positions, no new signals, after 4 trades opened
        # within a few minutes).
        now = datetime.utcnow()
        if now - self.state.hour_window_start >= timedelta(hours=1):
            self.state.trades_this_hour = 0
            self.state.hour_window_start = now
        if now.date() != self._day_date:
            self.state.trades_today = 0
            self._day_date = now.date()

        # 1-2: broker + account
        connected = await self.broker.is_connected()
        status = self.safety.check_broker_connection(connected)
        if not status.ok:
            self.notifications.broker_issue(status.reason or "disconnected")
            return

        account = await self.broker.get_account_info()
        self.safety.mark_data_received()
        self.state.peak_equity = max(self.state.peak_equity, account.equity)
        self.state.day_peak_equity = max(self.state.day_peak_equity, account.equity)

        dd_status = self.safety.check_drawdown(self.state.peak_equity, account.equity, self.state)
        if not dd_status.ok:
            self.notifications.safety_stop(dd_status.reason or "drawdown")
            await self.position_manager.emergency_close_all(await self.broker.get_open_positions(), "max_drawdown_kill_switch")
            return

        open_positions = await self.broker.get_open_positions()

        # 16: manage existing positions first every cycle
        await self._manage_open_positions(open_positions)

        if self.paused or self.state.kill_switch:
            return

        # 3-11: scan, regime, signals, confidence, ranking
        winrates = historical_winrates(self.db)
        candidates = await scan_markets(
            self.broker,
            [i for i in self.cfg.instruments],
            self.news_filter,
            historical_winrates=winrates,
            min_opportunity_score=max(
                self.cfg.get("scanning", "min_opportunity_score", default=65),
                self.optimizer.state.min_confidence * 0.8,
            ),
        )

        min_conf = max(
            self.cfg.get("confidence", "min_confidence_default", default=75),
            self.optimizer.state.min_confidence,
        )

        # Heartbeat: without this, a cycle where nothing qualifies produces
        # zero log output, which reads as "the bot stopped" even though it
        # is scanning and correctly waiting for a good enough setup.
        best_score = max((c.signal.confidence for c in candidates), default=0.0)
        log.info(
            "controller.scan_result",
            candidates_found=len(candidates),
            best_confidence=round(best_score, 1),
            min_confidence_required=min_conf,
            open_positions=len(open_positions),
        )

        for candidate in candidates:
            signal = candidate.signal
            if signal.confidence < min_conf:
                continue
            if signal.strategy.value in self.optimizer.state.disabled_strategies:
                continue

            self.db.log_decision("signal_considered", {
                "instrument": signal.instrument, "confidence": signal.confidence,
                "opportunity_score": candidate.opportunity_score, "reasons": signal.reasons,
            }, instrument=signal.instrument)

            opened = await self._try_open_trade(signal, account.equity, open_positions)
            if opened:
                open_positions = await self.broker.get_open_positions()

        # 18-19: analyze results, self-optimize (bounded)
        perf = compute_performance(self.db)
        changes = self.optimizer.optimize(perf)
        if changes:
            log.info("controller.optimization", changes=changes)

    async def _manage_open_positions(self, open_positions) -> None:
        for pos in open_positions:
            try:
                # The actual "current price" for stop-loss/take-profit and R-multiple
                # decisions MUST come from the live quote (a single, continuously
                # anchored tick), not from the last close of a freshly generated
                # candle series — get_candles() synthesizes a brand-new stochastic
                # path every call and its endpoint can jump far from the real
                # current price, which let losses blow past the stop-loss check.
                # Candles are only used here to compute ATR for the trailing stop.
                quote = await self.broker.get_quote(pos.instrument)
                current_price = quote.mid

                candles = await self.broker.get_candles(pos.instrument, "M5", 30)
                from tradingbot.indicators import atr as atr_fn
                import pandas as pd

                df = pd.DataFrame({"high": [c.high for c in candles], "low": [c.low for c in candles], "close": [c.close for c in candles]})
                current_atr = float(atr_fn(df, 14).iloc[-1]) if len(df) >= 14 else abs(pos.entry_price - pos.stop_loss)
            except RuntimeError as exc:
                # A single broker hiccup on one instrument must not stop
                # management of every other open position this cycle.
                log.error("position.manage_data_error", instrument=pos.instrument, error=str(exc))
                continue

            actions = await self.position_manager.manage(pos, current_price, current_atr, signal_still_valid=True)
            for action in actions:
                self.db.log_decision("position_management", {"action": action.action, "detail": action.detail}, instrument=pos.instrument)
                if action.action.startswith("exit"):
                    entry = pos.entry_price
                    direction_mult = 1 if pos.direction == Direction.LONG else -1
                    pnl = (current_price - entry) * direction_mult * pos.initial_quantity
                    r_mult = (current_price - entry) * direction_mult / abs(entry - pos.initial_stop_loss) if pos.initial_stop_loss != entry else 0.0
                    self.db.record_trade_close(pos.id, current_price, pnl, r_mult, action.action)
                    self.notifications.trade_closed(pos.instrument, pnl, r_mult)
                    if pos.direction == Direction.LONG and pnl < 0 or pos.direction == Direction.SHORT and pnl < 0:
                        self.state.consecutive_losses += 1
                    else:
                        self.state.consecutive_losses = 0

    async def _try_open_trade(self, signal, equity: float, open_positions) -> bool:
        instrument_currencies = INSTRUMENT_CURRENCIES.get(signal.instrument, [])
        blackout, reason = self.news_filter.is_blackout(instrument_currencies)
        if blackout:
            self.db.log_decision("trade_blocked", {"reason": reason}, instrument=signal.instrument)
            return False

        # Never stack multiple positions in the same instrument. The
        # correlation penalty below only ever softens position size — it
        # never blocks outright — so without this, the same repeated
        # signal (e.g. GBPUSD short at ~unchanged confidence every cycle)
        # kept opening a new position each cycle instead of managing the
        # one already open, multiplying exposure to a single idea instead
        # of diversifying (observed: 5 separate GBPUSD shorts opened within
        # 2 minutes, all at nearly the same price).
        if any(p.instrument == signal.instrument for p in open_positions):
            log.warning(
                "trade.blocked_duplicate_instrument",
                instrument=signal.instrument,
                confidence=signal.confidence,
            )
            self.db.log_decision("trade_blocked", {"reason": "duplicate_instrument"}, instrument=signal.instrument)
            return False

        corr_penalty = correlation_penalty(open_positions, signal.direction, signal.instrument)
        total_open_risk_pct = sum(getattr(p, "risk_amount", 0.0) for p in open_positions) / equity if equity else 0.0

        quote = await self.broker.get_quote(signal.instrument)
        profile = INSTRUMENT_PROFILES.get(signal.instrument, {})
        avg_spread = profile.get("spread_pips", 1.0) * profile.get("pip", 0.0001)
        spread_factor = quote.spread / avg_spread if avg_spread else 1.0
        volatility_factor = 1.0  # already embedded in regime filtering upstream

        decision = self.risk_manager.evaluate(
            confidence=signal.confidence,
            equity=equity,
            state=self.state,
            open_positions_count=len(open_positions),
            total_open_risk_pct=total_open_risk_pct,
            correlation_penalty=corr_penalty,
            volatility_factor=volatility_factor,
            spread_factor=spread_factor,
        )
        decision.risk_pct *= self.optimizer.state.risk_multiplier

        self.db.log_decision("risk_decision", {
            "risk_pct": decision.risk_pct, "blocked": decision.blocked,
            "block_reason": decision.block_reason, "reasons": decision.reasons,
        }, instrument=signal.instrument)

        if decision.blocked or decision.risk_pct <= 0:
            # Made visible in the terminal, not just the database — without
            # this, a blocked trade with a good-looking confidence score
            # (e.g. 83) looks like the bot is silently ignoring a clear
            # opportunity, when it's actually a risk-manager rule doing its
            # job (cooldown, max positions, drawdown, etc).
            log.warning(
                "trade.blocked_by_risk_manager",
                instrument=signal.instrument,
                confidence=signal.confidence,
                block_reason=decision.block_reason,
                reasons=decision.reasons,
            )
            if decision.block_reason in ("max_drawdown", "daily_loss_limit", "weekly_loss_limit"):
                self.notifications.risk_limit_hit(decision.block_reason)
            return False

        sizing = calculate_position_size(signal.instrument, equity, decision.risk_pct, signal.entry_price, signal.stop_loss)
        if sizing.lots_or_units <= 0:
            log.warning(
                "trade.blocked_zero_size",
                instrument=signal.instrument,
                risk_pct=decision.risk_pct,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
            )
            return False

        costs = estimate_costs(signal.instrument, sizing.lots_or_units, quote.spread)
        order_type = self.execution.choose_order_type(spread_factor * profile.get("spread_pips", 1.0), profile.get("spread_pips", 1.0))

        order = await self.execution.open_position(
            instrument=signal.instrument,
            direction=signal.direction,
            quantity=sizing.lots_or_units,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profits[0],
            order_type=order_type,
        )
        if not order:
            return False

        trade_id = order.id
        self.db.record_trade_open({
            "id": trade_id, "instrument": signal.instrument, "direction": signal.direction.value,
            "strategy": signal.strategy.value, "regime": signal.regime.value, "confidence": signal.confidence,
            "entry_price": order.filled_price, "quantity": sizing.lots_or_units, "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profits[0], "risk_amount": sizing.risk_amount,
            "opened_at": datetime.utcnow().isoformat(), "mode": self.cfg.mode,
        })
        self.state.trades_today += 1
        self.state.trades_this_hour += 1
        self.notifications.trade_opened(signal.instrument, signal.direction.value, signal.confidence)
        return True
