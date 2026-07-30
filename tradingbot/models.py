"""Core data models shared across the trading bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class MarketRegime(str, Enum):
    STRONG_UPTREND = "strong_uptrend"
    STRONG_DOWNTREND = "strong_downtrend"
    WEAK_TREND = "weak_trend"
    SIDEWAYS = "sideways"
    CONSOLIDATION = "consolidation"
    BREAKOUT = "breakout"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    LOW_LIQUIDITY = "low_liquidity"
    NEWS_VOLATILITY = "news_volatility"
    UNPREDICTABLE = "unpredictable"


class StrategyName(str, Enum):
    TREND_FOLLOWING = "trend_following"
    MOMENTUM_SCALPING = "momentum_scalping"
    BREAKOUT = "breakout"
    BREAKOUT_RETEST = "breakout_retest"
    PULLBACK = "pullback"
    MEAN_REVERSION = "mean_reversion"
    SUPPORT_RESISTANCE = "support_resistance"
    VWAP_REVERSION = "vwap_reversion"
    SESSION_BREAKOUT = "session_breakout"
    VOLATILITY_BREAKOUT = "volatility_breakout"


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class OpportunityScore:
    instrument: str
    score: float
    regime: MarketRegime
    reasons: list[str] = field(default_factory=list)


@dataclass
class Signal:
    instrument: str
    direction: Direction
    strategy: StrategyName
    regime: MarketRegime
    confidence: float
    entry_price: float
    stop_loss: float
    take_profits: list[float]
    atr: float
    reasons: list[str] = field(default_factory=list)
    expected_reward_r: float = 0.0
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RiskDecision:
    risk_pct: float
    reasons: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: Optional[str] = None


@dataclass
class PositionSizeResult:
    lots_or_units: float
    risk_amount: float
    stop_distance: float


@dataclass
class Order:
    id: str
    instrument: str
    direction: Direction
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: str = "pending"
    filled_price: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TakeProfitLevel:
    price: float
    close_fraction: float  # fraction of remaining position to close
    hit: bool = False


@dataclass
class Position:
    id: str
    instrument: str
    direction: Direction
    entry_price: float
    quantity: float
    initial_quantity: float
    stop_loss: float
    initial_stop_loss: float
    take_profit_levels: list[TakeProfitLevel]
    strategy: StrategyName
    regime_at_entry: MarketRegime
    confidence: float
    risk_amount: float
    opened_at: datetime = field(default_factory=datetime.utcnow)
    breakeven_moved: bool = False
    trailing_active: bool = False
    r_multiple_realized: float = 0.0
    reason: str = ""
