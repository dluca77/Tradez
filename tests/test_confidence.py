from __future__ import annotations

from tradingbot.confidence import ConfidenceInputs, compute_confidence


def _inputs(**overrides) -> ConfidenceInputs:
    base = dict(
        strategy_strength=0.8, htf_aligned=True, session_quality=1.0, spread_ratio=1.0,
        expected_rr=2.0, historical_winrate=0.6, liquidity_score=1.0, cost_to_profit_ratio=0.1,
    )
    base.update(overrides)
    return ConfidenceInputs(**base)


def test_confidence_within_bounds():
    score, _ = compute_confidence(_inputs())
    assert 0.0 <= score <= 100.0


def test_wide_spread_reduces_confidence():
    tight, _ = compute_confidence(_inputs(spread_ratio=1.0))
    wide, _ = compute_confidence(_inputs(spread_ratio=3.0))
    assert wide < tight


def test_high_cost_ratio_reduces_confidence():
    low_cost, _ = compute_confidence(_inputs(cost_to_profit_ratio=0.05))
    high_cost, _ = compute_confidence(_inputs(cost_to_profit_ratio=0.6))
    assert high_cost < low_cost


def test_htf_alignment_increases_confidence():
    aligned, _ = compute_confidence(_inputs(htf_aligned=True))
    not_aligned, _ = compute_confidence(_inputs(htf_aligned=False))
    assert aligned > not_aligned
