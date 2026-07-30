"""Confidence Scoring Engine: combines multiple factors into a 0-100 score.

Never relies on a single indicator — strategies already require multiple
confirmations to fire; this engine adds context-level confirmation on top
(higher-timeframe alignment, session quality, spread/liquidity, cost ratio,
risk-reward, and historical performance of similar signals).
"""
from __future__ import annotations

from dataclasses import dataclass

from tradingbot.strategies.base import StrategyResult


@dataclass
class ConfidenceInputs:
    strategy_strength: float          # 0..1 from the strategy itself
    htf_aligned: bool
    session_quality: float            # 0..1 (1 = prime session)
    spread_ratio: float               # current spread / average spread
    expected_rr: float                # reward:risk of first target
    historical_winrate: float | None  # 0..1 or None if no history
    liquidity_score: float            # 0..1
    cost_to_profit_ratio: float       # estimated cost / expected gross profit


def compute_confidence(inputs: ConfidenceInputs) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    score += inputs.strategy_strength * 40
    reasons.append(f"strategy strength {inputs.strategy_strength:.2f} -> +{inputs.strategy_strength*40:.1f}")

    if inputs.htf_aligned:
        score += 15
        reasons.append("higher-timeframe trend aligned -> +15")

    score += inputs.session_quality * 15
    reasons.append(f"session quality {inputs.session_quality:.2f} -> +{inputs.session_quality*15:.1f}")

    spread_penalty = max(0.0, (inputs.spread_ratio - 1.0)) * 15
    score -= min(spread_penalty, 15)
    if spread_penalty:
        reasons.append(f"spread {inputs.spread_ratio:.2f}x average -> -{min(spread_penalty,15):.1f}")

    # Full-target R:R (not just the conservative first partial-profit level),
    # since that's what the trade thesis is actually aiming for. A 2:1 or
    # better full target earns the max bonus here.
    rr_bonus = min(inputs.expected_rr / 2.5, 1.0) * 20
    score += rr_bonus
    reasons.append(f"expected R:R {inputs.expected_rr:.2f} -> +{rr_bonus:.1f}")

    if inputs.historical_winrate is not None:
        hist_bonus = (inputs.historical_winrate - 0.5) * 30
        score += hist_bonus
        reasons.append(f"historical winrate {inputs.historical_winrate:.2f} -> {hist_bonus:+.1f}")

    score += inputs.liquidity_score * 10
    reasons.append(f"liquidity {inputs.liquidity_score:.2f} -> +{inputs.liquidity_score*10:.1f}")

    if inputs.cost_to_profit_ratio > 0.3:
        penalty = min((inputs.cost_to_profit_ratio - 0.3) * 50, 20)
        score -= penalty
        reasons.append(f"cost/profit ratio {inputs.cost_to_profit_ratio:.2f} -> -{penalty:.1f}")

    score = max(0.0, min(100.0, score))
    return score, reasons
