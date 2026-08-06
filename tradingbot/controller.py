"""Autonomous Trading Controller: runs the full 20-step autonomous cycle
continuously. This is the single entry point that ties every module
together for paper trading (and, later, live trading with the same code
path against a real broker implementation).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import structlog

from tradingbot.broker.base import BrokerInterface
from tradingbot.broker.mock import INSTRUMENT_PROFILES
from tradingbot.config import AppConfig
from tradingbot.correlation import correlation_penalty
from tradingbot.costs import estimate_costs
from tradingbot.database import Database
from tradingbot.execution import OrderExecutionEngine
from tradingbot.models import Direction
from tradingbot.news_filter import INSTRUMENT_CURRENCIES, NewsFilter
from tradingbot.notifications import NotificationService, load_category_webhooks
from tradingbot.optimization import SelfOptimizationModule
from tradingbot.performance import (
    compute_performance,
    daily_pnl_summary,
    historical_winrates,
    per_instrument_pnl_today,
    weekly_pnl_summary,
)
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
        # The old single catch-all NOTIFY_WEBHOOK_URL posted to a different
        # (wrong, from Isaak's perspective) Discord server than the one the
        # interactive bot lives in - Isaak's call 2026-08-06: stop using it
        # entirely, route everything through the bot's own per-category
        # channels instead. Loaded synchronously here (not just set later by
        # the async bot on connect) so even the very first notification of
        # this run already goes to the right place, using whatever the bot
        # persisted on a previous run.
        self.notifications = NotificationService(
            enabled=cfg.get("notifications", "enabled", default=True),
            channel=cfg.get("notifications", "channel", default="log"),
            webhook_url="",
        )
        self.notifications.set_category_webhooks(load_category_webhooks())
        self.optimizer = SelfOptimizationModule(self.db, cfg.get("optimization", "min_sample_size", default=30))
        self.permanently_disabled_strategies = set(
            cfg.get("optimization", "permanently_disabled_strategies", default=[])
        )
        # Per-instrument strategy allowlist, evidence-based from real-data
        # backtests (e.g. breakout worked on XAUUSD but not XAGUSD/AUDUSD).
        # Empty/missing for an instrument means "no restriction beyond the
        # global permanently_disabled_strategies list above".
        self.instrument_allowed_strategies: dict[str, set[str]] = {
            instrument: set(strategies)
            for instrument, strategies in cfg.get("optimization", "instrument_strategies", default={}).items()
        }
        # Per-instrument strategy PARAMETER overrides (thresholds, ATR
        # multipliers, etc) — every instrument still runs every eligible
        # strategy, but each can tune how sensitive it is. Evidence-based,
        # filled in from backtest results, not guessed.
        self.instrument_strategy_params: dict[str, dict[str, dict]] = cfg.get(
            "optimization", "instrument_strategy_params", default={}
        )
        self.news_filter = NewsFilter(
            pre_minutes=cfg.get("news", "pre_event_blackout_minutes", default=30),
            post_minutes=cfg.get("news", "post_event_blackout_minutes", default=15),
            extreme_minutes=cfg.get("news", "high_impact_blackout_minutes", default=60),
            events_file=cfg.get("news", "events_file", default=None),
        )

        # scanner.py/costs.py/this module all fall back to {} for an
        # instrument missing from INSTRUMENT_PROFILES rather than raising -
        # necessary so one bad lookup can't take down a whole scan cycle,
        # but it means a newly enabled instrument with no matching profile
        # entry silently gets a ~10000x inflated spread_factor (division
        # against the {}.get("pip", 0.0001) default) instead of an error.
        # That crushes its confidence score and risk sizing to near zero -
        # indistinguishable from "no good setups" - for as long as nobody
        # notices. Confirmed live: UK100/JPN225 sat at ~0 trades for 5
        # hours after being enabled, capped at confidence ~59 vs a required
        # ~68, purely from this missing entry, not weak signals.
        missing_profiles = [i for i in cfg.instruments if i not in INSTRUMENT_PROFILES]
        if missing_profiles:
            log.error(
                "controller.instrument_missing_profile",
                instruments=missing_profiles,
                detail="spread/risk math will be badly wrong for these until "
                       "an entry is added to INSTRUMENT_PROFILES in broker/mock.py",
            )

        self.state = SessionState(
            day_start_equity=cfg.starting_balance,
            week_start_equity=cfg.starting_balance,
            month_start_equity=cfg.starting_balance,
            peak_equity=cfg.starting_balance,
            day_peak_equity=cfg.starting_balance,
        )
        self._positions_meta: dict[str, dict] = {}  # position_id -> signal metadata
        self._known_position_ids: set[str] = set()
        self.running = False
        self.paused = False
        self._day_date = datetime.utcnow().date()
        self._week_key = datetime.utcnow().isocalendar()[:2]
        # A blocked reason (daily_loss_limit etc.) used to re-notify Discord
        # on every single blocked candidate - with several candidates
        # scanned every ~15s, that was multiple pings per minute for
        # potentially hours (observed 2026-08-06: 5 messages within 2
        # seconds). Track which reasons have already been announced and
        # only notify once per reason per "episode" - cleared on day
        # rollover, since these are all daily/weekly checks.
        self._notified_risk_limits: set[str] = set()

    async def startup(self) -> None:
        await self.recovery.recover()
        self.safety.mark_data_received()

        persisted = self.db.load_session_state()

        # The session baselines must reflect the REAL broker's actual
        # starting equity, not cfg.starting_balance (a mock/papertrading
        # assumption of 10000). Against a real account with a different
        # balance (e.g. a 100000 MT5 demo account), comparing live equity
        # to the config's 10000 baseline produced a bogus "900% daily
        # profit" reading that immediately tripped the profit-lock and
        # blocked every trade from the first cycle onward.
        #
        # day_start_equity/day_peak_equity/week_start_equity/peak_equity are
        # restored from the database when available instead of always being
        # re-seeded from current equity - otherwise every restart silently
        # discarded that day's/week's/month's protection (a giveback or
        # profit-lock guard that had already proven itself would reset back
        # to "starts now"). A stale persisted day/week baseline (spanning an
        # actual calendar rollover while the process was down) self-corrects
        # on the first run_cycle() the same way a rollover during a run does.
        try:
            account = await self.broker.get_account_info()
            if persisted and persisted["day_start_equity"] is not None:
                self.state.day_start_equity = persisted["day_start_equity"]
                self.state.day_peak_equity = persisted["day_peak_equity"]
            else:
                self.state.day_start_equity = account.equity
                self.state.day_peak_equity = account.equity
            if persisted and persisted["week_start_equity"] is not None:
                self.state.week_start_equity = persisted["week_start_equity"]
            else:
                self.state.week_start_equity = account.equity
            if persisted and persisted["peak_equity"] is not None:
                self.state.peak_equity = max(persisted["peak_equity"], account.equity)
            else:
                self.state.peak_equity = account.equity
            self.state.month_start_equity = account.equity
            self.state.day_peak_equity = max(self.state.day_peak_equity, account.equity)
        except Exception as exc:  # noqa: BLE001
            log.error("controller.startup_equity_fetch_failed", error=str(exc))

        if persisted and persisted["week_key"]:
            year_str, week_str = persisted["week_key"].split("-")
            self._week_key = (int(year_str), int(week_str))

        # Restore risk/safety state (cooldowns, loss streaks, kill switch,
        # trade counters) that was in effect when the process last stopped.
        # Without this, every restart silently wiped these back to defaults
        # in memory, letting the bot bypass a cooldown or trade-limit block
        # that should still have applied.
        if persisted:
            self.state.consecutive_losses = persisted["consecutive_losses"]
            self.state.trades_today = persisted["trades_today"]
            self.state.trades_this_hour = persisted["trades_this_hour"]
            self.state.hour_window_start = datetime.fromisoformat(persisted["hour_window_start"])
            self.state.cooldown_until = (
                datetime.fromisoformat(persisted["cooldown_until"]) if persisted["cooldown_until"] else None
            )
            self.state.kill_switch = bool(persisted["kill_switch"])
            self.state.risk_scale = persisted["risk_scale"]
            self._day_date = datetime.fromisoformat(persisted["day_date"]).date()
            log.info(
                "controller.session_state_restored",
                consecutive_losses=self.state.consecutive_losses,
                cooldown_until=persisted["cooldown_until"],
                kill_switch=self.state.kill_switch,
                trades_today=self.state.trades_today,
            )

        self.optimizer.restore()
        log.info(
            "controller.optimization_state_restored",
            min_confidence=self.optimizer.state.min_confidence,
            risk_multiplier=self.optimizer.state.risk_multiplier,
            disabled_strategies=sorted(self.optimizer.state.disabled_strategies),
        )

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

    def _persist_state(self) -> None:
        self.db.save_session_state(
            consecutive_losses=self.state.consecutive_losses,
            trades_today=self.state.trades_today,
            trades_this_hour=self.state.trades_this_hour,
            hour_window_start=self.state.hour_window_start,
            cooldown_until=self.state.cooldown_until,
            kill_switch=self.state.kill_switch,
            risk_scale=self.state.risk_scale,
            day_date=self._day_date,
            day_start_equity=self.state.day_start_equity,
            day_peak_equity=self.state.day_peak_equity,
            week_start_equity=self.state.week_start_equity,
            peak_equity=self.state.peak_equity,
            week_key=f"{self._week_key[0]}-{self._week_key[1]}",
        )

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
        day_rolled = now.date() != self._day_date
        previous_day = self._day_date
        if day_rolled:
            self.state.trades_today = 0
            self._day_date = now.date()
            summary = daily_pnl_summary(self.db, previous_day)
            self.notifications.daily_summary(
                pnl=summary["total_pnl"], win_rate=summary["win_rate"],
                trades=summary["trades"], profit_factor=summary["profit_factor"],
            )
        week_rolled = now.isocalendar()[:2] != self._week_key
        previous_week_key = self._week_key
        if week_rolled:
            self._week_key = now.isocalendar()[:2]
            week_summary = weekly_pnl_summary(self.db, previous_week_key[0], previous_week_key[1])
            self.notifications.weekly_summary(
                pnl=week_summary["total_pnl"], win_rate=week_summary["win_rate"],
                trades=week_summary["trades"], profit_factor=week_summary["profit_factor"],
            )

        if day_rolled or week_rolled:
            self._notified_risk_limits.clear()

        # 1-2: broker + account
        connected = await self.broker.is_connected()
        status = self.safety.check_broker_connection(connected)
        if not status.ok:
            self.notifications.broker_issue(status.reason or "disconnected")
            return

        account = await self.broker.get_account_info()
        self.safety.mark_data_received()

        # day_start_equity/day_peak_equity/week_start_equity previously only
        # got set once in startup() and never again, so the profit-giveback,
        # daily-loss and weekly-loss checks kept comparing against the
        # FIRST day's baseline forever — a good day's peak could still block
        # every trade days later, and only a full process restart cleared
        # it (observed 2026-08-04: a giveback block from one trading day
        # was still active in the next run). Reset the baselines here on
        # the same calendar-day/week rollover that already resets the
        # trade counters above.
        if day_rolled:
            self.state.day_start_equity = account.equity
            self.state.day_peak_equity = account.equity
        if week_rolled:
            self.state.week_start_equity = account.equity
        if day_rolled or week_rolled:
            # Flush immediately rather than waiting for the next trade to
            # close - otherwise a crash/restart between the in-memory reset
            # above and the next trade-triggered _persist_state() would
            # reload yesterday's stale baseline from the database.
            self._persist_state()

        self.state.peak_equity = max(self.state.peak_equity, account.equity)
        self.state.day_peak_equity = max(self.state.day_peak_equity, account.equity)

        dd_status = self.safety.check_drawdown(self.state.peak_equity, account.equity, self.state)
        if not dd_status.ok:
            self.notifications.safety_stop(dd_status.reason or "drawdown")
            self._persist_state()
            await self.position_manager.emergency_close_all(await self.broker.get_open_positions(), "max_drawdown_kill_switch")
            return

        open_positions = await self.broker.get_open_positions()

        # Broker adapters (e.g. MT5Broker.get_open_positions) build a fresh
        # Position on every poll and have no way to know when it was really
        # opened, so Position.opened_at defaults to "now" every single call.
        # That silently disables the max-hold time-based exit below (it
        # never sees more than a few seconds of elapsed time) for any
        # position recovered from the broker rather than opened by us this
        # process lifetime - restore the real value from our own trade log,
        # which is written with datetime.utcnow() at the moment we placed it.
        true_opened_at = {
            row["id"]: datetime.fromisoformat(row["opened_at"])
            for row in self.db.fetch_open_trades()
        }
        for pos in open_positions:
            if pos.id in true_opened_at:
                pos.opened_at = true_opened_at[pos.id]

        # A position can disappear from the broker between polls without
        # this bot ever calling close_position() itself — MT5 executes
        # stop-loss/take-profit natively at the server. Without this check,
        # such a trade is simply lost: never recorded as closed, missing
        # from "recent closed trades", and not counted for win-rate/streak
        # tracking.
        current_ids = {p.id for p in open_positions}
        vanished_ids = self._known_position_ids - current_ids
        for trade_id in vanished_ids:
            await self._record_vanished_trade(trade_id)
        self._known_position_ids = current_ids

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
            instrument_strategy_params=self.instrument_strategy_params,
            db=self.db,
        )

        # Base bar: still adapts over time from real win-rate evidence (see
        # optimization.py), so it isn't a number we picked — but it's a
        # single floor, and a single floor can't tell a lopsided-reward
        # trade apart from a break-even-ish one. The floor alone is not
        # the bar a signal has to clear.
        base_conf = max(
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
            base_confidence_floor=base_conf,
            open_positions=len(open_positions),
        )

        for candidate in candidates:
            signal = candidate.signal
            # The bar a signal must clear is not one fixed number. A trade
            # offering a big payoff for the risk taken (high expected_reward_r)
            # is worth entering even when the bot is only moderately sure,
            # because the wins more than cover the extra losses; a trade with
            # a thin payoff needs the bot to be much more convinced before
            # it's worth the risk at all. This shifts the bar per signal
            # instead of measuring every signal against the same number.
            # rr=1.5 is the pivot: richer reward lowers the bar, thinner
            # reward raises it, each clamped so the bar stays sane.
            rr_shift = max(-20.0, min(25.0, (1.5 - signal.expected_reward_r) * 12.0))
            min_conf = max(50.0, min(95.0, base_conf + rr_shift))
            # Every one of these filters used to `continue` silently before
            # anything was logged — a candidate blocked here (e.g. by the
            # per-instrument strategy allowlist) never showed up anywhere,
            # not even in the /signalen decision log, making it look like
            # the bot had gone quiet when it was actually filtering signals
            # out every cycle.
            if signal.confidence < min_conf:
                self.db.log_decision("signal_filtered", {
                    "reason": "confidence_below_threshold", "confidence": signal.confidence,
                    "min_confidence_required": round(min_conf, 1),
                    "expected_reward_r": round(signal.expected_reward_r, 2),
                    "strategy": signal.strategy.value,
                }, instrument=signal.instrument)
                continue
            if signal.strategy.value in self.optimizer.state.disabled_strategies:
                self.db.log_decision("signal_filtered", {
                    "reason": "strategy_disabled_by_optimizer", "strategy": signal.strategy.value,
                }, instrument=signal.instrument)
                continue
            if signal.strategy.value in self.permanently_disabled_strategies:
                self.db.log_decision("signal_filtered", {
                    "reason": "strategy_permanently_disabled", "strategy": signal.strategy.value,
                }, instrument=signal.instrument)
                continue
            allowed_for_instrument = self.instrument_allowed_strategies.get(signal.instrument)
            if allowed_for_instrument and signal.strategy.value not in allowed_for_instrument:
                self.db.log_decision("signal_filtered", {
                    "reason": "strategy_not_allowed_for_instrument", "strategy": signal.strategy.value,
                    "allowed": sorted(allowed_for_instrument),
                }, instrument=signal.instrument)
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

    async def _record_vanished_trade(self, trade_id: str) -> None:
        local_rows = [row for row in self.db.fetch_open_trades() if row["id"] == trade_id]
        if not local_rows:
            return  # nothing we know about locally; nothing to record
        row = local_rows[0]

        result = await self.broker.get_closed_position_result(trade_id)
        if result is not None:
            exit_price, pnl = result
            direction_mult = 1 if row["direction"] == "long" else -1
            risk_per_unit = abs(row["entry_price"] - row["stop_loss"]) if row["stop_loss"] else 0.0
            r_mult = (
                (exit_price - row["entry_price"]) * direction_mult / risk_per_unit
                if risk_per_unit else 0.0
            )
            exit_reason = "exit_stop_loss" if pnl < 0 else "exit_take_profit"
        else:
            # Broker can't tell us what happened (e.g. MockBroker, or a
            # history lookup failure) — record the closure without
            # fabricating a P&L rather than silently dropping the trade.
            exit_price, pnl, r_mult = row["entry_price"], 0.0, 0.0
            exit_reason = "closed_at_broker_unknown_pnl"

        self.db.record_trade_close(trade_id, exit_price, pnl, r_mult, exit_reason)
        self.notifications.trade_closed(row["instrument"], pnl, r_mult)
        self._register_trade_result(pnl)
        log.info(
            "trade.closed_at_broker",
            trade_id=trade_id,
            instrument=row["instrument"],
            pnl=pnl,
            r_multiple=round(r_mult, 2),
            exit_reason=exit_reason,
        )

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
                import pandas as pd

                from tradingbot.indicators import atr as atr_fn

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
                    r_mult = (current_price - entry) * direction_mult / abs(entry - pos.initial_stop_loss) if pos.initial_stop_loss != entry else 0.0

                    # Prefer the broker's own authoritative realized P&L
                    # (e.g. MT5's history_deals_get, which sums every exit
                    # deal for this position ticket including earlier
                    # partial closes, and correctly accounts for contract
                    # size). Our manual estimate below uses `quantity`,
                    # which for a real MT5 position is the broker's LOT
                    # size, not the underlying-unit quantity our own sizing
                    # computed — multiplying price-diff by lots directly
                    # understated pnl by the contract-size factor (e.g.
                    # ~100x on XAUUSD, where 1 lot = 100 oz).
                    broker_result = await self.broker.get_closed_position_result(pos.id)
                    if broker_result is not None:
                        exit_price, pnl = broker_result
                    else:
                        exit_price = current_price
                        # Use the REMAINING quantity for this final leg, plus
                        # whatever was already realized by earlier partial
                        # closes — using initial_quantity here would apply the
                        # final price to the whole original size, silently
                        # discarding profit already locked in at 1.5R/2R.
                        pnl = pos.realized_pnl + (current_price - entry) * direction_mult * pos.quantity

                    self.db.record_trade_close(pos.id, exit_price, pnl, r_mult, action.action)
                    self.notifications.trade_closed(pos.instrument, pnl, r_mult)
                    self._register_trade_result(pnl)

    def _register_trade_result(self, pnl: float) -> None:
        if pnl < 0:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.cfg.risk.max_consecutive_losses and not self.state.cooldown_until:
                # Start an actual cooldown timer instead of leaving the bot
                # blocked indefinitely — cooldown_minutes_after_losses was
                # configured but never used anywhere before this.
                minutes = self.cfg.risk.cooldown_minutes_after_losses
                self.state.cooldown_until = datetime.utcnow() + timedelta(minutes=minutes)
                log.warning(
                    "risk.cooldown_started",
                    consecutive_losses=self.state.consecutive_losses,
                    cooldown_until=self.state.cooldown_until.isoformat(),
                )
        else:
            self.state.consecutive_losses = 0
            self.state.cooldown_until = None
        self._persist_state()

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

        instrument_pnl_today = per_instrument_pnl_today(self.db, self._day_date).get(signal.instrument, 0.0)
        instrument_daily_pnl_pct = instrument_pnl_today / equity if equity else 0.0

        decision = self.risk_manager.evaluate(
            confidence=signal.confidence,
            equity=equity,
            state=self.state,
            open_positions_count=len(open_positions),
            total_open_risk_pct=total_open_risk_pct,
            correlation_penalty=corr_penalty,
            volatility_factor=volatility_factor,
            spread_factor=spread_factor,
            instrument_daily_pnl_pct=instrument_daily_pnl_pct,
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
            if (
                decision.block_reason in ("max_drawdown", "daily_loss_limit", "weekly_loss_limit")
                and decision.block_reason not in self._notified_risk_limits
            ):
                self._notified_risk_limits.add(decision.block_reason)
                self.notifications.risk_limit_hit(decision.block_reason)
            return False

        # calculate_position_size() assumes 1 unit of quote-currency price
        # movement equals 1 unit of account currency - true enough for
        # USD/GBP instruments on a EUR account to not matter much, but
        # wildly wrong for a JPY-denominated instrument (~155-180:1),
        # confirmed live on JPN225: real risk taken landed ~180x smaller
        # than intended. get_risk_conversion_factor() corrects for it using
        # the broker's own live tick economics.
        conversion_factor = await self.broker.get_risk_conversion_factor(signal.instrument)
        sizing = calculate_position_size(
            signal.instrument, equity, decision.risk_pct * conversion_factor, signal.entry_price, signal.stop_loss
        )
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
            # Broker-side hard TP is a safety net at the strategy's furthest
            # target, not a cap at the first one - the position manager
            # (position_manager.py) is the one that actually banks partial
            # profit at 1.5R/2R and trails the remainder. Using
            # take_profits[0] here made the broker auto-close every winner
            # at ~1R before that logic ever ran, silently turning an
            # intended asymmetric (up to 2.5R) payoff into a near-1:1
            # bracket - confirmed live on 2026-08-04 (10 stop-outs averaging
            # -1.00R vs 7 take-profits averaging only +0.90R).
            take_profit=signal.take_profits[-1],
            order_type=order_type,
        )
        if not order:
            # execution.open_position already logs the concrete reason
            # (zero quantity / broker rejected / not filled) to the
            # terminal, but that trace never reached the /signalen decision
            # log or dashboard — a signal that passed every check (good
            # confidence, risk ok) but then silently failed on the actual
            # order placement looked identical to "still being considered",
            # cycle after cycle, with zero visible reason why it never
            # became a trade.
            self.db.log_decision("trade_blocked", {
                "reason": "order_not_filled", "strategy": signal.strategy.value,
            }, instrument=signal.instrument)
            return False

        trade_id = order.id
        self.db.record_trade_open({
            "id": trade_id, "instrument": signal.instrument, "direction": signal.direction.value,
            "strategy": signal.strategy.value, "regime": signal.regime.value, "confidence": signal.confidence,
            "entry_price": order.filled_price, "quantity": sizing.lots_or_units, "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profits[-1], "risk_amount": sizing.risk_amount,
            "opened_at": datetime.utcnow().isoformat(), "mode": self.cfg.mode,
        })
        self.state.trades_today += 1
        self.state.trades_this_hour += 1
        self._persist_state()
        self.notifications.trade_opened(signal.instrument, signal.direction.value, signal.confidence)
        return True
