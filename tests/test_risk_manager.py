from __future__ import annotations

from tradingbot.config import RiskConfig
from tradingbot.risk_manager import DynamicRiskManager, SessionState, confidence_to_base_risk


def _cfg() -> RiskConfig:
    return RiskConfig(
        default_risk_pct=0.0025, min_risk_pct=0.001, max_risk_pct=0.0075, absolute_max_risk_pct=0.0075,
        max_daily_loss_pct=0.02, max_weekly_loss_pct=0.05, max_monthly_drawdown_pct=0.10,
        max_total_open_risk_pct=0.02, max_concurrent_positions=3, max_trades_per_hour=4,
        max_trades_per_day=10, max_consecutive_losses=4, cooldown_minutes_after_losses=60,
        daily_profit_lock_pct=0.02, daily_profit_soft_lock_pct=0.015, daily_profit_giveback_pct=0.5,
        martingale_forbidden=True,
    )


def _state(equity: float = 10_000.0) -> SessionState:
    return SessionState(
        day_start_equity=equity, week_start_equity=equity, month_start_equity=equity,
        peak_equity=equity, day_peak_equity=equity,
    )


def test_confidence_tiers():
    cfg = _cfg()
    assert confidence_to_base_risk(60, cfg) == 0.0
    assert confidence_to_base_risk(72, cfg) == 0.0010
    assert confidence_to_base_risk(85, cfg) == 0.0025
    assert confidence_to_base_risk(92, cfg) == 0.0050
    assert confidence_to_base_risk(97, cfg) == cfg.max_risk_pct


def test_risk_never_exceeds_absolute_max():
    cfg = _cfg()
    mgr = DynamicRiskManager(cfg)
    decision = mgr.evaluate(
        confidence=99, equity=10_000, state=_state(), open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
    )
    assert decision.risk_pct <= cfg.absolute_max_risk_pct


def test_kill_switch_blocks_trading():
    cfg = _cfg()
    mgr = DynamicRiskManager(cfg)
    state = _state()
    state.kill_switch = True
    decision = mgr.evaluate(
        confidence=95, equity=10_000, state=state, open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
    )
    assert decision.blocked
    assert decision.risk_pct == 0.0


def test_no_martingale_risk_decreases_after_losses():
    cfg = _cfg()
    mgr = DynamicRiskManager(cfg)
    state_normal = _state()
    state_losses = _state()
    state_losses.consecutive_losses = 3

    normal = mgr.evaluate(
        confidence=90, equity=10_000, state=state_normal, open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
    )
    after_losses = mgr.evaluate(
        confidence=90, equity=10_000, state=state_losses, open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
    )
    assert after_losses.risk_pct < normal.risk_pct


def test_daily_loss_limit_blocks_trading_below_raised_bar():
    cfg = _cfg()
    mgr = DynamicRiskManager(cfg)
    state = _state(equity=10_000)
    equity_after_loss = 10_000 * (1 - cfg.max_daily_loss_pct - 0.001)
    decision = mgr.evaluate(
        confidence=80, equity=equity_after_loss, state=state, open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
    )
    assert decision.blocked
    assert decision.block_reason == "daily_loss_limit"


def test_daily_loss_limit_allows_confidence_at_or_above_raised_bar():
    cfg = _cfg()
    mgr = DynamicRiskManager(cfg)
    state = _state(equity=10_000)
    equity_after_loss = 10_000 * (1 - cfg.max_daily_loss_pct - 0.001)
    decision = mgr.evaluate(
        confidence=cfg.daily_loss_limit_min_confidence, equity=equity_after_loss, state=state,
        open_positions_count=0, total_open_risk_pct=0.0, correlation_penalty=1.0,
        volatility_factor=1.0, spread_factor=1.0,
    )
    assert not decision.blocked


def test_max_concurrent_positions_blocks():
    cfg = _cfg()
    mgr = DynamicRiskManager(cfg)
    decision = mgr.evaluate(
        confidence=95, equity=10_000, state=_state(), open_positions_count=cfg.max_concurrent_positions,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
    )
    assert decision.blocked
    assert decision.block_reason == "max_positions"


def test_instrument_daily_loss_limit_blocks_only_that_instrument():
    cfg = _cfg()
    cfg.max_instrument_daily_loss_pct = 0.015
    mgr = DynamicRiskManager(cfg)

    losing_instrument = mgr.evaluate(
        confidence=95, equity=10_000, state=_state(), open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
        instrument_daily_pnl_pct=-0.02,
    )
    assert losing_instrument.blocked
    assert losing_instrument.block_reason == "instrument_daily_loss_limit"

    other_instrument = mgr.evaluate(
        confidence=95, equity=10_000, state=_state(), open_positions_count=0,
        total_open_risk_pct=0.0, correlation_penalty=1.0, volatility_factor=1.0, spread_factor=1.0,
        instrument_daily_pnl_pct=0.0,
    )
    assert not other_instrument.blocked
