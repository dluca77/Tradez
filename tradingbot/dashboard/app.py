"""Read-only dashboard + simple control endpoints (start/pause/kill-switch/flatten).

Run alongside the controller in the same process (see run_paper.py).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from tradingbot.controller import AutonomousTradingController
from tradingbot.performance import compute_performance

TEMPLATE = """<!doctype html>
<html><head><title>Autonomous Trading Bot</title>
<meta http-equiv="refresh" content="5">
<style>
body{{font-family:system-ui,sans-serif;background:#0b0e14;color:#e6e6e6;margin:0;padding:24px}}
.card{{background:#161b26;border-radius:10px;padding:16px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.stat{{font-size:1.4rem;font-weight:600}}
.label{{color:#9aa4b2;font-size:.8rem;text-transform:uppercase}}
.pos{{color:#3ddc84}} .neg{{color:#ff5c5c}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:6px;border-bottom:1px solid #232a38;text-align:left}}
</style></head>
<body>
<h2>Autonomous Trading Bot — {mode}</h2>
<div class="grid">
<div class="card"><div class="label">Balance</div><div class="stat">{balance:.2f}</div></div>
<div class="card"><div class="label">Equity</div><div class="stat">{equity:.2f}</div></div>
<div class="card"><div class="label">Margin Available</div><div class="stat">{margin:.2f}</div></div>
<div class="card"><div class="label">Open Positions</div><div class="stat">{open_positions}</div></div>
<div class="card"><div class="label">Consecutive Losses</div><div class="stat">{losses}</div></div>
<div class="card"><div class="label">Kill Switch</div><div class="stat">{kill}</div></div>
<div class="card"><div class="label">Total Trades</div><div class="stat">{total_trades}</div></div>
<div class="card"><div class="label">Win Rate</div><div class="stat">{win_rate:.1%}</div></div>
<div class="card"><div class="label">Total PnL</div><div class="stat {pnl_class}">{total_pnl:.2f}</div></div>
</div>
<div class="card"><h3>Notes</h3><p>Use POST /control/pause, /control/resume, /control/kill, /control/flatten.</p></div>
</body></html>"""


def create_app(controller: AutonomousTradingController) -> FastAPI:
    app = FastAPI(title="Autonomous Trading Bot Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def home():
        account = await controller.broker.get_account_info()
        positions = await controller.broker.get_open_positions()
        perf = compute_performance(controller.db)
        return TEMPLATE.format(
            mode=controller.cfg.mode,
            balance=account.balance,
            equity=account.equity,
            margin=account.margin_available,
            open_positions=len(positions),
            losses=controller.state.consecutive_losses,
            kill="ACTIVE" if controller.state.kill_switch else "off",
            total_trades=perf.total_trades,
            win_rate=perf.win_rate,
            total_pnl=perf.total_pnl,
            pnl_class="pos" if perf.total_pnl >= 0 else "neg",
        )

    @app.get("/api/status")
    async def status():
        account = await controller.broker.get_account_info()
        positions = await controller.broker.get_open_positions()
        return {
            "mode": controller.cfg.mode,
            "live_trading_enabled": controller.cfg.live_trading_enabled,
            "balance": account.balance,
            "equity": account.equity,
            "margin_available": account.margin_available,
            "open_positions": [p.instrument for p in positions],
            "kill_switch": controller.state.kill_switch,
            "paused": controller.paused,
        }

    @app.get("/api/performance")
    async def performance():
        perf = compute_performance(controller.db)
        return {
            "total_trades": perf.total_trades,
            "win_rate": perf.win_rate,
            "total_pnl": perf.total_pnl,
            "avg_r": perf.avg_r,
            "profit_factor": perf.profit_factor,
            "by_instrument": perf.by_instrument,
            "by_strategy": perf.by_strategy,
        }

    @app.post("/control/pause")
    async def pause():
        controller.paused = True
        return {"paused": True}

    @app.post("/control/resume")
    async def resume():
        controller.paused = False
        return {"paused": False}

    @app.post("/control/kill")
    async def kill():
        controller.safety.manual_kill_switch(controller.state, "dashboard_manual")
        return {"kill_switch": True}

    @app.post("/control/flatten")
    async def flatten():
        positions = await controller.broker.get_open_positions()
        await controller.position_manager.emergency_close_all(positions, "manual_flatten")
        return {"closed": len(positions)}

    return app
