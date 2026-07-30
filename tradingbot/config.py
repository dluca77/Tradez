"""Configuration loading: config.yaml + environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class RiskConfig:
    default_risk_pct: float
    min_risk_pct: float
    max_risk_pct: float
    absolute_max_risk_pct: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_monthly_drawdown_pct: float
    max_total_open_risk_pct: float
    max_concurrent_positions: int
    max_trades_per_hour: int
    max_trades_per_day: int
    max_consecutive_losses: int
    cooldown_minutes_after_losses: int
    daily_profit_lock_pct: float
    daily_profit_soft_lock_pct: float
    daily_profit_giveback_pct: float
    martingale_forbidden: bool


@dataclass
class AppConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        return self.raw["mode"]

    @property
    def live_trading_enabled(self) -> bool:
        return bool(self.raw.get("live_trading_enabled", False))

    @property
    def instruments(self) -> list[str]:
        return list(self.raw["instruments"]["enabled"])

    @property
    def crypto_enabled(self) -> bool:
        return bool(self.raw["instruments"].get("crypto_enabled", False))

    @property
    def risk(self) -> RiskConfig:
        r = self.raw["risk"]
        return RiskConfig(**r)

    @property
    def starting_balance(self) -> float:
        return float(self.raw["account"]["starting_balance"])

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path) if path else ROOT_DIR / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig(raw=raw)


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)
