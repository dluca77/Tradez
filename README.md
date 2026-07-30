# Tradez — Autonomous AI Trading Bot

A fully autonomous trading system for traditional financial markets (gold,
silver, major FX pairs, and optionally major indices). After one-time
installation, broker linking, and activation, the bot analyzes markets,
selects instruments and direction, sizes risk, opens and manages trades,
protects capital, and recovers from failures — all without manual
intervention. Crypto is disabled by default.

**Current state of this repository: a fully working, tested paper-trading
system.** Live trading is intentionally not wired to a real broker yet — see
[Roadmap to live trading](#roadmap-to-live-trading) below. Nothing here
places real orders until you explicitly build and enable a live broker
adapter.

## Architecture

25 modules under `tradingbot/`, each with a single responsibility:

| # | Module | File |
|---|--------|------|
| 1 | Autonomous Trading Controller | `controller.py` |
| 2 | Market Data Service | `broker/mock.py` (`get_candles`/`get_quote`) |
| 3 | Market Scanner | `scanner.py` |
| 4 | Market Regime Detector | `regime.py` |
| 5 | Signal Engine | `strategies/base.py` |
| 6 | Strategy Selector | `strategy_selector.py` |
| 7 | Opportunity Ranking Engine | `scanner.py` (`Candidate.opportunity_score`) |
| 8 | Confidence Scoring Engine | `confidence.py` |
| 9 | Dynamic Risk Manager | `risk_manager.py` |
| 10 | Position Sizing Engine | `position_sizing.py` |
| 11 | Order Execution Engine | `execution.py` |
| 12 | Position Management Engine | `position_manager.py` |
| 13 | News Filter | `news_filter.py` |
| 14 | Correlation Manager | `correlation.py` |
| 15 | Portfolio Risk Manager | `risk_manager.py` + `controller.py` |
| 16 | Broker Integration Layer | `broker/base.py`, `broker/mock.py` |
| 17 | Backtesting Engine | `backtest.py` |
| 18 | Paper Trading Engine | `run_paper.py` + `broker/mock.py` |
| 19 | Performance Analytics | `performance.py` |
| 20 | Self-Optimization Module | `optimization.py` |
| 21 | Safety and Kill-Switch Module | `safety.py` |
| 22 | Recovery and Reconciliation Service | `recovery.py` |
| 23 | Notification Service | `notifications.py` |
| 24 | Dashboard | `dashboard/app.py` |
| 25 | Logging and Audit System | `database.py` (decisions/parameter_changes/safety_events tables), `logging_config.py` |

The same `AutonomousTradingController` code path drives backtesting, paper
trading, and (once implemented) live trading — only the `BrokerInterface`
implementation changes.

## Quick start (paper trading)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in values only if/when you add a real broker
python run_paper.py
```

This starts:
- the **Autonomous Trading Controller**, running the full 20-step cycle every
  `scanning.scan_interval_seconds` (config.yaml), against a simulated
  `MockBroker` that generates realistic price action, spreads, and fills —
  no real orders, ever, in this mode;
- a **dashboard** at `http://localhost:8080` showing balance, equity, open
  positions, win rate, PnL, and safety status, with simple controls
  (`/control/pause`, `/control/resume`, `/control/kill`, `/control/flatten`).

## Docker

```bash
docker compose up --build
```

Mounts `./data` and `./logs` so the SQLite database and logs persist across
container restarts, and `./config.yaml` so you can tune limits without
rebuilding the image.

## Configuration

- `config.yaml` — instruments, timeframes, risk limits, confidence
  thresholds, session preferences, news blackout windows, dashboard port.
- `.env` (from `.env.example`) — broker credentials and API keys. **Never**
  hardcoded in source; loaded via `python-dotenv` in `tradingbot/config.py`.

Key defaults (all overridable, all bounded — see `config.yaml`):

- Risk per trade: 0.10%-0.75%, default ~0.25%, tiered by confidence score.
- Max daily loss 2%, max weekly loss 5%, max monthly drawdown 10%.
- Max 3 concurrent positions, max 10 trades/day, max 4 trades/hour.
- Stops after 4 consecutive losses; risk scales down (never up) after losses.
- Minimum confidence to trade: 75, auto-raised in poor conditions.
- News blackout: 30 min before / 15 min after high-impact events (longer for
  extreme events).

## Running tests

```bash
pytest tests/ -v
```

26 unit tests cover indicators, all 10 strategies, confidence scoring, the
dynamic risk manager (including the no-martingale guarantee and every hard
limit), position sizing, and correlation exposure.

## Backtesting

```python
from tradingbot.backtest import run_backtest
# supply df_exec (execution timeframe OHLCV) and df_ctx (higher-timeframe OHLCV)
result = run_backtest("EURUSD", df_exec, df_ctx, starting_equity=10_000)
print(result.total_pnl, result.win_rate)
```

The backtester replays bar-by-bar with no look-ahead (a strategy only ever
sees data up to and including the current bar) and applies spread,
commission, and slippage costs before recording PnL.

## Safety design

- **Kill switch**: manual (`/control/kill`) or automatic on max drawdown.
- **Hard risk limits** (`risk_manager.py`) are ceilings the self-optimization
  module (`optimization.py`) can only tighten, never loosen — it cannot
  raise risk limits, raise the drawdown ceiling, enable martingale, or
  disable any safety check; those setters simply do not exist on its
  bounded state object.
- **No martingale**: risk is derived from confidence and scaled down after
  losing streaks; it is never scaled up to chase losses.
- **Stop-loss can only tighten**: `position_manager._tighten_only` makes it
  structurally impossible to widen a stop to give a losing trade more room.
- **Daily profit protection**: locks in gains once a daily profit target is
  hit, and stops if the bot gives back too much of the day's peak profit.
- **Recovery**: on every startup, `recovery.py` reconnects to the broker,
  reconciles local trade records against actual broker positions, and only
  allows new trades once reconciliation completes.
- **Audit logging**: every signal considered, risk decision, position
  management action, parameter change, and safety event is written to
  SQLite (`decisions`, `parameter_changes`, `safety_events`, `trades`
  tables) — nothing is a black box.

## Roadmap to live trading

This repository intentionally ships the paper-trading path first, per the
required build order. To add live trading safely:

1. Implement a concrete `BrokerInterface` (see `tradingbot/broker/base.py`)
   for a regulated broker with an official API — MetaTrader 5, OANDA, or
   Interactive Brokers are natural first targets. Never scrape a broker's
   website or use unofficial login flows.
2. Validate the broker connection and credentials end-to-end in a demo/practice
   account.
3. Run the backtesting engine across representative history for every
   enabled instrument/strategy pair.
4. Run paper trading (this repo's default mode) for an extended period and
   review `performance.py` output.
5. Only then flip `live_trading_enabled: true` in `config.yaml` and swap the
   `MockBroker` in `run_paper.py`/a new `run_live.py` for the real broker
   adapter — one explicit, reviewed step, never automatic.

## Project layout

```
tradingbot/
  broker/            broker interface + mock/paper broker
  strategies/         10 strategies + shared interface
  dashboard/          FastAPI dashboard
  controller.py        the autonomous trading cycle
  scanner.py            market scanner / opportunity ranking
  regime.py             market regime detection
  confidence.py         confidence scoring engine
  risk_manager.py        dynamic risk manager + hard limits
  position_sizing.py      position sizing
  execution.py             order execution engine
  position_manager.py      open-position management (BE, trailing, partials)
  correlation.py            correlation / hidden-exposure manager
  news_filter.py             economic news blackout windows
  costs.py                     transaction cost estimation
  sessions.py                   trading session quality scoring
  performance.py                  performance analytics
  optimization.py                  bounded self-optimization
  safety.py                         kill-switch / safety module
  recovery.py                        startup reconciliation
  notifications.py                    user notifications
  database.py                          SQLite persistence + audit log
  backtest.py                           backtesting engine
tests/                                   unit tests
run_paper.py                              paper trading entrypoint
config.yaml                                all tunable parameters
.env.example                                credential template
Dockerfile / docker-compose.yml              containerized deployment
```
