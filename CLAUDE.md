# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Autonomous MT5 trading bot (Python) trading XAUUSD, NAS100, SPX500. Single package `tradingbot/`,
orchestrated by `tradingbot/controller.py::AutonomousTradingController` (scan → regime → signals →
confidence → risk → execution → position mgmt → optimization).

## Hard rule: never hard-exclude a strategy per instrument

Strategies must never be hard-excluded on a per-instrument basis. Every instrument must remain
free to run every strategy in `STRATEGY_REGISTRY` (`tradingbot/strategies/base.py`) and produce its
own signal — bad backtest evidence for a strategy/instrument pair is addressed by **tuning
parameters**, never by excluding the strategy. This has been tried and explicitly reverted before
(see git history around `de2d984`/`cc1c38e`/`71730c2`).

Concretely, in `config.yaml`'s `optimization` block:
- `instrument_strategy_params` — the ONLY place to adjust per-instrument strategy behavior
  (thresholds like `adx_threshold`, `stop_atr_mult`, `lookback`, etc.).
- `permanently_disabled_strategies` and `instrument_strategies` (allowlist) — must stay `[]`/`{}`.
  Do not populate these; doing so silently re-enables hard exclusion via the filtering logic in
  `controller.py` (~lines 292–308).
- Restricting an entire **instrument** in `instruments.enabled` (not a strategy) is a different,
  allowed kind of restriction — that's an evidence-based instrument-viability decision, not a
  strategy exclusion.

The one legitimate way a strategy stops firing per-instrument is `SelfOptimizationModule.optimize()`
(`tradingbot/optimization.py`) — an adaptive, reversible, evidence-based runtime mechanism (≥15 real
trades, win rate <35%, negative avg R), not a pre-set config exclusion.

## Risk-sensitive changes require confirmation

The repo trades against a real MT5 demo account (MetaQuotes-Demo, live credentials in `.env`).
Always confirm with the user before changing: `mode`, `broker.name`, `live_trading_enabled`, or any
`risk.*` value in `config.yaml`, even though this is a demo account. Note `config.yaml` currently has
uncommitted changes moving it from `mode: paper`/`broker: mock` to `mode: live`/`broker: mt5` —
don't assume this is finished/intentional without checking with the user, and don't commit it
without asking.

## Environment / credentials

- `.env` (gitignored) must set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (required by
  `tradingbot/broker/mt5.py`, raises if unset). Never print or commit its contents.
- Optional: `MT5_SYMBOL_SUFFIX`, `MT5_SYMBOL_OVERRIDES` (comma-separated `KEY=VALUE`, e.g.
  `NAS100=USTEC,SPX500=US500`) — needed because MetaQuotes-Demo lists indices under different
  tickers than the internal `MT5_SYMBOL_MAP`.
- The `MetaTrader5` Python package is intentionally absent from `requirements.txt` — it's a deferred
  import that only works where an actual MT5 terminal can run (native Windows or Wine); install it
  manually when working on the live broker path.
- `live_gate.py` hard-codes an additional live-trading activation gate on top of
  `live_trading_enabled`: `MIN_PAPER_TRADES=30`, `MIN_PAPER_WIN_RATE=0.40`,
  `MIN_PAPER_PROFIT_FACTOR=1.0`.

## Commands

- Tests: `pytest tests/ -v` (asyncio_mode=auto via `pytest.ini`; no CI configured, run locally).
- Backtest (Yahoo data): `python run_backtest.py XAUUSD --yfinance --period 60d --interval 5m`
  (ticker map in `tradingbot/data_loader.py::YAHOO_TICKERS`); or `--csv path` for local data.
- Backtest (MT5 history): `run_mt5_backtest.py` pulls a year of M5 history from a logged-in MT5
  terminal — each chart must first be scrolled back manually in the terminal so
  `copy_rates_range` has cached data (see script docstring).
- Real-data viability sweep: `run_backtest_check.py` — this generated the evidence behind
  `instruments.enabled` in `config.yaml`.
- Run paper/live: `python run_paper.py` / `python run_live.py` (gated, see above).

## Workflow

Work happens via direct commits on `claude/autonomous-ai-trading-bot-tfx5dh` — no PR/CI process.
Commit messages are short, imperative, present-tense, and often state the reasoning inline (e.g.
"Stop hard-excluding momentum_scalping/vwap_reversion").

## Known drift

`README.md` is stale in several places (calls MT5 "stubbed" though it's fully implemented; cites
old confidence/position-limit numbers; cites 10 strategies/26 tests though there are now 11
strategies). Treat `config.yaml` and the code as ground truth over `README.md`.
