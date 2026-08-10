"""Market Scanner + Opportunity Ranking Engine.

Scans all enabled instruments, evaluates regime + strategy signals, scores
each opportunity 0-100, and returns candidates sorted best-first.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingbot.broker.base import BrokerInterface
from tradingbot.broker.mock import INSTRUMENT_PROFILES
from tradingbot.confidence import ConfidenceInputs, compute_confidence
from tradingbot.costs import cost_to_profit_ratio, estimate_costs
from tradingbot.models import Direction, MarketRegime, Signal
from tradingbot.news_filter import INSTRUMENT_CURRENCIES, NewsFilter
from tradingbot.position_sizing import MIN_STOP_DISTANCE_PCT
from tradingbot.regime import detect_regime
from tradingbot.sessions import instrument_cooldown_active, session_quality
from tradingbot.strategy_selector import generate_signals


def _candles_to_df(candles) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [c.time for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )


@dataclass
class Candidate:
    signal: Signal
    opportunity_score: float
    regime: MarketRegime
    reasons: list[str]


def _log_skip(db, instrument: str, reason: str, **extra) -> None:
    # Every scan-cycle skip below used to be a bare `continue` — an
    # instrument stuck in an untradeable regime, or one that never
    # produces a strategy signal, left zero trace anywhere, so it looked
    # indistinguishable from "the scanner isn't checking this instrument
    # at all" (observed: only one of three configured instruments ever
    # showed up in the decision log/dashboard, with no visible reason).
    if db is not None:
        db.log_decision("scan_skipped", {"reason": reason, **extra}, instrument=instrument)


async def scan_markets(
    broker: BrokerInterface,
    instruments: list[str],
    news_filter: NewsFilter,
    historical_winrates: dict[str, float] | None = None,
    min_opportunity_score: float = 65.0,
    instrument_strategy_params: dict[str, dict[str, dict]] | None = None,
    instrument_session_cooldowns: dict[str, dict] | None = None,
    db=None,
) -> list[Candidate]:
    historical_winrates = historical_winrates or {}
    instrument_strategy_params = instrument_strategy_params or {}
    candidates: list[Candidate] = []
    sess_quality, sess_name = session_quality()

    for instrument in instruments:
        cooldown, cooldown_reason = instrument_cooldown_active(instrument, instrument_session_cooldowns)
        if cooldown:
            _log_skip(db, instrument, "session_cooldown", detail=cooldown_reason)
            continue

        currencies = INSTRUMENT_CURRENCIES.get(instrument, [])
        blackout, reason = news_filter.is_blackout(currencies)
        if blackout:
            _log_skip(db, instrument, "news_blackout", detail=reason)
            continue

        try:
            candles_exec = await broker.get_candles(instrument, "M5", 150)
            candles_ctx = await broker.get_candles(instrument, "H1", 150)
        except RuntimeError as exc:
            # One broken/unavailable instrument (e.g. a symbol the real
            # broker doesn't offer under this name) must not take down the
            # whole scan cycle — skip it and keep scanning the rest.
            _log_skip(db, instrument, "broker_data_error", detail=str(exc))
            continue
        df_exec = _candles_to_df(candles_exec)
        df_ctx = _candles_to_df(candles_ctx)
        if len(df_exec) < 30:
            _log_skip(db, instrument, "insufficient_bars", bars=len(df_exec))
            continue

        quote = await broker.get_quote(instrument)
        profile = INSTRUMENT_PROFILES.get(instrument, {})
        avg_spread = profile.get("spread_pips", 1.0) * profile.get("pip", 0.0001)
        spread_pips = quote.spread / profile.get("pip", 0.0001) if profile.get("pip") else 0.0
        avg_spread_pips = profile.get("spread_pips", 1.0)

        regime = detect_regime(df_exec, spread_pips, avg_spread_pips)
        # LOW_LIQUIDITY is an actual cost fact (spread blown out relative to
        # normal), not a label-based exclusion — trading is genuinely too
        # expensive right now regardless of strategy. Every other regime,
        # including UNPREDICTABLE, still lets every strategy look at the
        # candles and decide for itself; the regime label only informs
        # confidence scoring below, it never blocks a strategy outright.
        if regime == MarketRegime.LOW_LIQUIDITY:
            _log_skip(db, instrument, "regime_not_tradeable", regime=regime.value)
            continue

        strategy_signals = generate_signals(
            df_exec, df_ctx, regime, instrument_strategy_params.get(instrument)
        )
        if not strategy_signals:
            _log_skip(db, instrument, "no_strategy_signal", regime=regime.value)
            continue

        for strategy_name, result in strategy_signals:
            # Rebase the strategy's entry/stop/targets onto the REAL current
            # quote. Strategies compute entry_price from the last candle
            # close of a synthetic history that can drift from the live
            # price by the time the signal is actually acted on — without
            # this, position sizing and stop-loss distance get calculated
            # against a stale reference price, which let losses land far
            # outside the intended risk (observed: -0.65R actually meant
            # -EUR464 instead of the ~EUR75 that R was supposed to represent).
            # Shifting everything by the same offset preserves the
            # strategy's intended risk:reward shape while anchoring it to
            # reality.
            offset = quote.mid - result.entry_price
            entry_price = quote.mid
            stop_loss = result.stop_loss + offset
            take_profits = [tp + offset for tp in result.take_profits]

            # position_sizing.py floors the distance it sizes the position
            # against (to stop a near-zero stop from blowing quantity up),
            # but until now that floor only affected the quantity math —
            # the stop actually sent to the broker stayed at its original,
            # tighter distance. That mismatch meant the dollar amount lost
            # when a tight-stop trade (momentum_scalping, vwap_reversion)
            # hit its stop was smaller than the risk_pct the trade was
            # sized for, while a normal-stop trade (trend_following,
            # pullback) lost the full intended amount — so "-1.00R" ended
            # up meaning wildly different euro amounts depending on which
            # strategy opened the trade. Widening the real stop here, at
            # the source, keeps sizing and the actual stop in lockstep.
            min_distance = entry_price * MIN_STOP_DISTANCE_PCT
            if abs(entry_price - stop_loss) < min_distance:
                stop_loss = (
                    entry_price - min_distance if result.direction == Direction.LONG
                    else entry_price + min_distance
                )

            spread_factor = quote.spread / avg_spread if avg_spread else 1.0
            htf_aligned = True  # strategies already check HTF where relevant
            # Use the full/final target for expected R:R (what the trade
            # thesis actually aims for), not just the conservative first
            # partial-profit level — the latter under-scores every signal.
            final_target = take_profits[-1]
            expected_rr = abs(final_target - entry_price) / abs(entry_price - stop_loss) if stop_loss != entry_price else 0.0

            quantity_placeholder = 1.0
            costs = estimate_costs(instrument, quantity_placeholder, quote.spread)
            expected_gross = abs(take_profits[0] - entry_price) * quantity_placeholder
            cost_ratio = cost_to_profit_ratio(costs.total, expected_gross)

            liquidity_score = 1.0 - min(spread_factor - 1.0, 1.0) if spread_factor > 1 else 1.0
            hist_wr = historical_winrates.get(f"{instrument}:{strategy_name.value}")

            confidence, conf_reasons = compute_confidence(
                ConfidenceInputs(
                    strategy_strength=result.strength,
                    htf_aligned=htf_aligned,
                    session_quality=sess_quality,
                    spread_ratio=spread_factor,
                    expected_rr=expected_rr,
                    historical_winrate=hist_wr,
                    liquidity_score=max(0.0, liquidity_score),
                    cost_to_profit_ratio=cost_ratio,
                )
            )

            if cost_ratio > 0.35:
                _log_skip(db, instrument, "cost_too_high", strategy=strategy_name.value, cost_ratio=cost_ratio)
                continue

            signal = Signal(
                instrument=instrument,
                direction=result.direction,
                strategy=strategy_name,
                regime=regime,
                confidence=confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profits=take_profits,
                atr=result.atr,
                reasons=result.reasons + conf_reasons + [f"session={sess_name}"],
                expected_reward_r=expected_rr,
            )

            opportunity_score = confidence * 0.7 + min(expected_rr, 3.0) / 3.0 * 20 + sess_quality * 10
            if opportunity_score < min_opportunity_score:
                _log_skip(
                    db, instrument, "opportunity_score_too_low",
                    strategy=strategy_name.value, score=round(opportunity_score, 1),
                    min_required=round(min_opportunity_score, 1),
                )
                continue

            candidates.append(
                Candidate(signal=signal, opportunity_score=opportunity_score, regime=regime, reasons=signal.reasons)
            )

    candidates.sort(key=lambda c: (c.opportunity_score, c.signal.expected_reward_r), reverse=True)
    return candidates
