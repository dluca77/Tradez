"""Bayesian hyperparameter search (Optuna) for a strategy's tunable params,
on real Yahoo Finance data, with a train/validation split so results aren't
just fit to noise in one period - the same anti-overfitting practice
documented for freqtrade's hyperopt: optimize on one slice, confirm the edge
holds on a held-out slice before trusting it.

Replaces hand-picking instrument_strategy_params values by eyeballing a
single backtest run with a systematic search plus an honest overfit check.

Usage:
    python run_hyperopt.py XAUUSD trend_following
    python run_hyperopt.py NAS100 vwap_reversion --trials 60
"""
from __future__ import annotations

import argparse
import sys

import optuna

from tradingbot.backtest import run_backtest
from tradingbot.data_loader import load_from_yfinance, resample

optuna.logging.set_verbosity(optuna.logging.WARNING)

# Per-strategy search space: param -> (low, high, is_int). Ranges are
# centered on strategies/base.py's defaults, widened enough to let the
# search actually move, not so wide it wastes trials on absurd values.
SEARCH_SPACE = {
    "trend_following": {"adx_threshold": (15, 35, True), "stop_atr_mult": (1.0, 3.0, False)},
    "momentum_scalping": {"rsi_up": (50, 70, True), "rsi_down": (30, 50, True), "stop_atr_mult": (0.5, 2.0, False)},
    "breakout": {"lookback": (10, 40, True), "stop_atr_mult": (0.8, 2.0, False)},
    "breakout_retest": {"stop_atr_mult": (0.5, 2.0, False), "retest_tolerance": (0.0005, 0.003, False)},
    "pullback": {
        "pullback_atr_mult": (0.3, 1.0, False), "rsi_low": (30, 45, True),
        "rsi_high": (55, 70, True), "stop_atr_mult": (0.3, 1.5, False),
    },
    "mean_reversion": {
        "bb_std": (1.5, 3.0, False), "rsi_oversold": (20, 35, True),
        "rsi_overbought": (65, 80, True), "stop_atr_mult": (0.5, 2.0, False),
    },
    "support_resistance": {"proximity_atr_mult": (0.2, 0.8, False), "stop_atr_mult": (0.5, 2.0, False)},
    "vwap_reversion": {"dist_atr_mult": (1.0, 3.0, False), "stop_atr_mult": (0.5, 2.0, False)},
    "session_breakout": {"vol_mult": (1.0, 2.0, False), "stop_atr_mult": (0.8, 2.0, False)},
    "volatility_breakout": {
        "expansion_mult": (1.1, 1.8, False), "squeeze_mult": (0.5, 1.0, False), "stop_atr_mult": (0.8, 2.0, False),
    },
}


def _score(df_exec, df_ctx, instrument: str, params: dict) -> tuple[float, int]:
    result = run_backtest(instrument, df_exec, df_ctx, strategy_params=params)
    wins = sum(t.pnl for t in result.trades if t.pnl > 0)
    losses = abs(sum(t.pnl for t in result.trades if t.pnl <= 0))
    pf = wins / losses if losses > 0 else (float("inf") if wins > 0 else 0.0)
    return pf, len(result.trades)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("instrument")
    parser.add_argument("strategy", choices=sorted(SEARCH_SPACE))
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument(
        "--min-trades", type=int, default=15,
        help="min trades on the TRAIN split for a trial to score above 0 - "
             "a param combo that only fires twice and wins both looks like "
             "'inf' profit factor but is noise, not edge.",
    )
    args = parser.parse_args()

    df_exec = load_from_yfinance(args.instrument, period="60d", interval="5m")
    df_ctx = resample(df_exec, "1h")

    split = int(len(df_exec) * 0.65)
    train_exec = df_exec.iloc[:split].reset_index(drop=True)
    val_exec = df_exec.iloc[split:].reset_index(drop=True)
    split_time = train_exec["time"].iloc[-1]
    train_ctx = df_ctx[df_ctx["time"] <= split_time].reset_index(drop=True)
    val_ctx = df_ctx[df_ctx["time"] > split_time].reset_index(drop=True)

    space = SEARCH_SPACE[args.strategy]

    def objective(trial: optuna.Trial) -> float:
        params = {}
        for name, (lo, hi, is_int) in space.items():
            params[name] = trial.suggest_int(name, int(lo), int(hi)) if is_int else trial.suggest_float(name, lo, hi)
        pf, n_trades = _score(train_exec, train_ctx, args.instrument, {args.strategy: params})
        if n_trades < args.min_trades:
            return 0.0
        return pf

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=args.trials, show_progress_bar=False)

    best_params = study.best_params
    train_pf, train_n = _score(train_exec, train_ctx, args.instrument, {args.strategy: best_params})
    val_pf, val_n = _score(val_exec, val_ctx, args.instrument, {args.strategy: best_params})

    print(f"\n{args.instrument} / {args.strategy} - best params: {best_params}")
    print(f"  train:      profit_factor={train_pf:.2f} ({train_n} trades)")
    print(f"  validation: profit_factor={val_pf:.2f} ({val_n} trades)  <- held out, NOT optimized on")
    if train_pf >= 1.0 > val_pf:
        print("  WARNING: looks overfit to the train split - validation doesn't confirm the edge. Do not use these params as-is.")
    elif val_n < args.min_trades:
        print(f"  NOTE: validation split only had {val_n} trades - too thin to trust either way, treat as inconclusive.")


if __name__ == "__main__":
    sys.exit(main())
