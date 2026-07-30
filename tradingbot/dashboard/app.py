"""Read-only dashboard + simple control endpoints (start/pause/kill-switch/flatten).

Run alongside the controller in the same process (see run_paper.py).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from tradingbot.controller import AutonomousTradingController
from tradingbot.models import Direction
from tradingbot.performance import compute_performance

TEMPLATE = """<!doctype html>
<html><head><title>Trading Bot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    background: #0b0e14; color: #e8ebf0; margin: 0;
    padding: 16px 16px 48px; max-width: 720px; margin-inline: auto;
  }}
  h1 {{ font-size: 1.15rem; font-weight: 600; margin: 4px 0 2px; }}
  .subtitle {{ color: #7d8896; font-size: .85rem; margin-bottom: 18px; }}
  .pill {{
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: .75rem; font-weight: 600; margin-left: 6px;
  }}
  .pill-on {{ background: #1d3a2a; color: #3ddc84; }}
  .pill-off {{ background: #2a2020; color: #ff6b6b; }}
  .pill-paper {{ background: #1e2a3a; color: #6fb3ff; }}
  section {{ margin-bottom: 22px; }}
  .section-title {{
    font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
    color: #7d8896; margin-bottom: 8px; font-weight: 600;
  }}
  .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
  .card {{
    background: #151a24; border: 1px solid #232a38; border-radius: 14px;
    padding: 14px;
  }}
  .card .label {{ color: #7d8896; font-size: .75rem; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.5rem; font-weight: 700; line-height: 1.15; }}
  .card .sub {{ font-size: .8rem; color: #7d8896; margin-top: 2px; }}
  .pos {{ color: #3ddc84; }} .neg {{ color: #ff6b6b; }} .neutral {{ color: #e8ebf0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
  th {{ text-align: left; color: #7d8896; font-weight: 500; padding: 6px 4px; border-bottom: 1px solid #232a38; }}
  td {{ padding: 8px 4px; border-bottom: 1px solid #1c2330; }}
  .empty {{ color: #5a6472; font-size: .85rem; padding: 10px 4px; }}
  .dir-long {{ color: #3ddc84; font-weight: 600; }}
  .dir-short {{ color: #ff6b6b; font-weight: 600; }}
  .btn-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  button {{
    flex: 1; min-width: 130px; padding: 12px; border-radius: 10px; border: none;
    font-size: .85rem; font-weight: 600; color: #e8ebf0; cursor: pointer;
  }}
  .btn-pause {{ background: #2a2f3d; }}
  .btn-resume {{ background: #1d3a2a; color: #3ddc84; }}
  .btn-flatten {{ background: #3a2f1d; color: #f0a93d; }}
  .btn-kill {{ background: #3a2020; color: #ff6b6b; }}
  .note {{ font-size: .78rem; color: #5a6472; margin-top: 10px; }}
  #toast {{
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #1d3a2a; color: #3ddc84; padding: 10px 18px; border-radius: 10px;
    font-size: .85rem; display: none;
  }}
</style></head>
<body>

<h1>Autonomous Trading Bot
  <span class="pill pill-paper">{mode_label}</span>
  <span class="pill {kill_pill_class}">{kill_label}</span>
</h1>
<div class="subtitle">Laatst bijgewerkt: automatisch elke 10 sec &middot; ververs handmatig voor de nieuwste stand</div>

<section>
  <div class="section-title">Account</div>
  <div class="grid">
    <div class="card">
      <div class="label">Balans (gerealiseerd)</div>
      <div class="value">&euro;{balance:,.2f}</div>
      <div class="sub">Startkapitaal + gesloten trades</div>
    </div>
    <div class="card">
      <div class="label">Equity (incl. open posities)</div>
      <div class="value {equity_class}">&euro;{equity:,.2f}</div>
      <div class="sub {equity_class}">{equity_change_sign}{equity_change_pct:.2f}% t.o.v. balans</div>
    </div>
    <div class="card">
      <div class="label">Beschikbare marge</div>
      <div class="value">&euro;{margin:,.2f}</div>
    </div>
    <div class="card">
      <div class="label">Open posities</div>
      <div class="value">{open_positions}</div>
    </div>
  </div>
</section>

<section>
  <div class="section-title">Prestaties (afgesloten trades)</div>
  <div class="grid">
    <div class="card">
      <div class="label">Totaal trades gesloten</div>
      <div class="value">{total_trades}</div>
      <div class="sub">Nodig voor live: 30</div>
    </div>
    <div class="card">
      <div class="label">Win rate</div>
      <div class="value">{win_rate:.2f}%</div>
      <div class="sub">Doel: &gt; 40%</div>
    </div>
    <div class="card">
      <div class="label">Totale winst/verlies</div>
      <div class="value {pnl_class}">{pnl_sign}&euro;{total_pnl_abs:,.2f}</div>
      <div class="sub {pnl_class}">{pnl_sign}{total_pnl_pct:.3f}% van startkapitaal</div>
    </div>
    <div class="card">
      <div class="label">Profit factor</div>
      <div class="value">{profit_factor:.2f}</div>
      <div class="sub">Doel: &gt; 1.00</div>
    </div>
  </div>
</section>

<section>
  <div class="section-title">Veiligheid</div>
  <div class="grid">
    <div class="card">
      <div class="label">Verliezen op rij</div>
      <div class="value">{losses}</div>
      <div class="sub">Stopt na 4</div>
    </div>
    <div class="card">
      <div class="label">Modus</div>
      <div class="value" style="font-size:1.1rem">{paused_label}</div>
    </div>
  </div>
</section>

<section>
  <div class="section-title">Open posities</div>
  {positions_table}
</section>

<section>
  <div class="section-title">Recent gesloten trades</div>
  {recent_trades_table}
</section>

<section>
  <div class="section-title">Bediening</div>
  <div class="btn-row">
    <button class="btn-pause" onclick="callControl('/control/pause')">&#9208; Pauzeer</button>
    <button class="btn-resume" onclick="callControl('/control/resume')">&#9654; Hervat</button>
    <button class="btn-flatten" onclick="if(confirm('Alle open posities nu sluiten?')) callControl('/control/flatten')">&#128721; Sluit alles</button>
    <button class="btn-kill" onclick="if(confirm('Noodstop activeren? De bot stopt dan volledig tot een herstart.')) callControl('/control/kill')">&#9888; Noodstop</button>
  </div>
  <div class="note">"Pauzeer" stopt nieuwe trades maar blijft open posities beheren. "Noodstop" stopt alles hard.</div>
</section>

<div id="toast"></div>
<script>
async function callControl(path) {{
  const res = await fetch(path, {{ method: 'POST' }});
  const toast = document.getElementById('toast');
  toast.textContent = res.ok ? 'Gelukt' : 'Mislukt';
  toast.style.display = 'block';
  setTimeout(() => toast.style.display = 'none', 1800);
  setTimeout(() => location.reload(), 900);
}}
</script>
</body></html>"""

POSITION_ROW = """<tr>
  <td>{instrument}</td>
  <td class="dir-{direction_class}">{direction_label}</td>
  <td>{entry_price:.5f}</td>
  <td>{current_price:.5f}</td>
  <td class="{pnl_class}">{pnl_sign}&euro;{pnl:,.2f}</td>
</tr>"""

TRADE_ROW = """<tr>
  <td>{instrument}</td>
  <td class="dir-{direction_class}">{direction_label}</td>
  <td>{strategy}</td>
  <td class="{pnl_class}">{pnl_sign}&euro;{pnl:,.2f}</td>
  <td>{exit_reason}</td>
</tr>"""


def _direction_label(direction_value: str) -> tuple[str, str]:
    if direction_value == Direction.LONG.value:
        return "long", "Long"
    return "short", "Short"


def create_app(controller: AutonomousTradingController) -> FastAPI:
    app = FastAPI(title="Autonomous Trading Bot Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def home():
        account = await controller.broker.get_account_info()
        positions = await controller.broker.get_open_positions()
        perf = compute_performance(controller.db)
        starting_balance = controller.cfg.starting_balance

        equity_change_pct = ((account.equity - account.balance) / account.balance * 100) if account.balance else 0.0
        total_pnl_pct = (perf.total_pnl / starting_balance * 100) if starting_balance else 0.0

        rows = []
        for pos in positions:
            try:
                quote = await controller.broker.get_quote(pos.instrument)
                current_price = quote.mid
            except Exception:
                current_price = pos.entry_price
            direction_mult = 1 if pos.direction == Direction.LONG else -1
            pnl = (current_price - pos.entry_price) * direction_mult * pos.quantity
            dclass, dlabel = _direction_label(pos.direction.value)
            rows.append(POSITION_ROW.format(
                instrument=pos.instrument, direction_class=dclass, direction_label=dlabel,
                entry_price=pos.entry_price, current_price=current_price,
                pnl_class="pos" if pnl >= 0 else "neg", pnl_sign="+" if pnl >= 0 else "-", pnl=abs(pnl),
            ))
        positions_table = (
            "<table><tr><th>Instrument</th><th>Richting</th><th>Instap</th><th>Nu</th><th>Winst/Verlies</th></tr>"
            + "".join(rows) + "</table>"
        ) if rows else '<div class="empty">Geen open posities op dit moment.</div>'

        closed = controller.db.fetch_closed_trades()[:10]
        trade_rows = []
        for row in closed:
            pnl = row["pnl"] or 0.0
            dclass, dlabel = _direction_label(row["direction"])
            trade_rows.append(TRADE_ROW.format(
                instrument=row["instrument"], direction_class=dclass, direction_label=dlabel,
                strategy=row["strategy"], pnl_class="pos" if pnl >= 0 else "neg",
                pnl_sign="+" if pnl >= 0 else "-", pnl=abs(pnl),
                exit_reason=row["exit_reason"] or "-",
            ))
        recent_trades_table = (
            "<table><tr><th>Instrument</th><th>Richting</th><th>Strategie</th><th>Resultaat</th><th>Reden</th></tr>"
            + "".join(trade_rows) + "</table>"
        ) if trade_rows else '<div class="empty">Nog geen trades afgesloten.</div>'

        return TEMPLATE.format(
            mode_label=controller.cfg.mode.upper(),
            kill_label="NOODSTOP ACTIEF" if controller.state.kill_switch else "Veilig",
            kill_pill_class="pill-off" if controller.state.kill_switch else "pill-on",
            balance=account.balance,
            equity=account.equity,
            equity_class="pos" if account.equity >= account.balance else "neg",
            equity_change_sign="+" if equity_change_pct >= 0 else "",
            equity_change_pct=equity_change_pct,
            margin=account.margin_available,
            open_positions=len(positions),
            total_trades=perf.total_trades,
            win_rate=perf.win_rate * 100,
            pnl_class="pos" if perf.total_pnl >= 0 else "neg",
            pnl_sign="+" if perf.total_pnl >= 0 else "-",
            total_pnl_abs=abs(perf.total_pnl),
            total_pnl_pct=abs(total_pnl_pct),
            profit_factor=perf.profit_factor,
            losses=controller.state.consecutive_losses,
            paused_label="Gepauzeerd" if controller.paused else "Actief aan het zoeken",
            positions_table=positions_table,
            recent_trades_table=recent_trades_table,
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
