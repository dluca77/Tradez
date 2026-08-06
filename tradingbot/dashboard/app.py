"""Read-only dashboard + simple control endpoints (start/pause/kill-switch/flatten).

Run alongside the controller in the same process (see run_paper.py).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from tradingbot.controller import AutonomousTradingController
from tradingbot.models import Direction
from tradingbot.performance import compute_performance, equity_curve

# All timestamps are stored in the database as naive UTC (datetime.utcnow()).
# Displaying them as-is silently showed UTC as if it were local time, running
# 2 hours behind the user's actual clock (Netherlands, CEST in summer).
_LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

TEMPLATE = """<!doctype html>
<html><head><title>Trading Bot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    background: radial-gradient(ellipse 900px 500px at 50% -10%, #131a2a 0%, #0b0e14 55%) #0b0e14;
    background-attachment: fixed;
    color: #e8ebf0; margin: 0;
    padding: 16px 16px 48px; max-width: 720px; margin-inline: auto;
  }}
  h1 {{ font-size: 1.15rem; font-weight: 600; margin: 4px 0 2px; display: flex; align-items: center; gap: 4px; }}
  .subtitle {{ color: #7d8896; font-size: .85rem; margin-bottom: 18px; }}
  .live-dot {{
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: #3ddc84; margin-left: 4px; flex-shrink: 0;
    box-shadow: 0 0 0 0 rgba(61, 220, 132, .6);
    animation: livePulse 2s ease-out infinite;
  }}
  @keyframes livePulse {{
    0% {{ box-shadow: 0 0 0 0 rgba(61, 220, 132, .55); }}
    70% {{ box-shadow: 0 0 0 7px rgba(61, 220, 132, 0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(61, 220, 132, 0); }}
  }}
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
  html, body {{ overflow-x: hidden; width: 100%; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
  .card {{
    background: #151a24; border: 1px solid #232a38; border-radius: 14px;
    padding: 14px; min-width: 0;
    box-shadow: 0 1px 2px rgba(0,0,0,.2);
    transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
  }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,.28); border-color: #2a3040; }}
  /* Entrance animation: every section fades/slides in on load, staggered
     by DOM order via nth-of-type. Replays on each 10s auto-refresh - by
     design, since a full-page <meta refresh> means there's no persistent
     DOM to animate FROM a previous value TO a new one; a brief, gentle
     restatement on each refresh reads as "this just updated" rather than
     a jarring reload. Short duration + reduced-motion opt-out keep it
     from feeling gimmicky. */
  @keyframes fadeSlideIn {{
    from {{ opacity: 0; transform: translateY(6px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}
  section {{ animation: fadeSlideIn .5s ease both; }}
  section:nth-of-type(1) {{ animation-delay: .02s; }}
  section:nth-of-type(2) {{ animation-delay: .07s; }}
  section:nth-of-type(3) {{ animation-delay: .12s; }}
  section:nth-of-type(4) {{ animation-delay: .17s; }}
  section:nth-of-type(5) {{ animation-delay: .22s; }}
  section:nth-of-type(6) {{ animation-delay: .27s; }}
  section:nth-of-type(n+7) {{ animation-delay: .3s; }}
  .hero {{ animation: fadeSlideIn .5s ease both; }}
  @media (prefers-reduced-motion: reduce) {{
    section, .hero, .live-dot {{ animation: none !important; }}
    .card {{ transition: none !important; }}
  }}
  .card .label {{ color: #7d8896; font-size: .75rem; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.5rem; font-weight: 700; line-height: 1.15; overflow-wrap: break-word; word-break: break-word; }}
  .card .sub {{ font-size: .8rem; color: #7d8896; margin-top: 2px; overflow-wrap: break-word; }}
  @media (max-width: 380px) {{
    .card .value {{ font-size: 1.25rem; }}
  }}
  .pos {{ color: #3ddc84; }} .neg {{ color: #ff6b6b; }} .neutral {{ color: #e8ebf0; }}
  .table-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
  th {{ text-align: left; color: #7d8896; font-weight: 500; padding: 6px 4px; border-bottom: 1px solid #232a38; white-space: nowrap; }}
  td {{ padding: 8px 4px; border-bottom: 1px solid #1c2330; white-space: nowrap; }}
  .empty {{ color: #5a6472; font-size: .85rem; padding: 10px 4px; }}
  .dimmed {{ color: #7d8896; }}
  .view-all {{ display: inline-block; margin-top: 10px; font-size: .82rem; color: #6fb3ff; text-decoration: none; }}
  .view-all:hover {{ text-decoration: underline; }}
  .dir-long {{ color: #3ddc84; font-weight: 600; }}
  .dir-short {{ color: #ff6b6b; font-weight: 600; }}
  /* On narrow screens, tables collapse into stacked cards instead of a
     scrollable wide table — a horizontally-scrolling table is easy to miss
     entirely on a phone (no visible scrollbar), so rows become their own
     labeled card that fits the screen without any sideways swipe. */
  @media (max-width: 640px) {{
    .table-scroll table, .table-scroll thead, .table-scroll tbody,
    .table-scroll th, .table-scroll td, .table-scroll tr {{ display: block; }}
    .table-scroll thead {{ display: none; }}
    .table-scroll tr {{
      border: 1px solid #232a38; border-radius: 10px; padding: 4px 10px;
      margin-bottom: 8px; background: #10141c;
    }}
    .table-scroll td {{
      display: flex; justify-content: space-between; align-items: center;
      white-space: normal; border-bottom: 1px solid #1c2330; padding: 7px 0;
      text-align: right;
    }}
    .table-scroll td:last-child {{ border-bottom: none; }}
    .table-scroll td::before {{
      content: attr(data-label); color: #7d8896; font-weight: 500;
      text-align: left; padding-right: 10px;
    }}
  }}
  .btn-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  button {{
    flex: 1 1 130px; min-width: 0; padding: 12px; border-radius: 10px; border: none;
    font-size: .85rem; font-weight: 600; color: #e8ebf0; cursor: pointer;
  }}
  .btn-pause {{ background: #2a2f3d; }}
  .btn-resume {{ background: #1d3a2a; color: #3ddc84; }}
  .btn-flatten {{ background: #3a2f1d; color: #f0a93d; }}
  .btn-kill {{ background: #3a2020; color: #ff6b6b; }}
  .note {{ font-size: .78rem; color: #5a6472; margin-top: 10px; }}
  .chart-card {{
    background: #151a24; border: 1px solid #232a38; border-radius: 14px;
    padding: 12px; margin-bottom: 10px;
  }}
  .chart-title {{ font-size: .85rem; margin-bottom: 6px; }}
  .chart-title .dir-long, .chart-title .dir-short {{ margin: 0 4px; }}
  canvas.candles {{ width: 100%; height: 160px; display: block; }}
  .chart-meta {{ font-size: .78rem; color: #9aa4b2; margin-top: 8px; line-height: 1.5; }}
  .chart-meta b {{ color: #e8ebf0; }}
  .chart-meta .next-step {{ color: #6fb3ff; }}
  .chart-toggle {{
    margin-top: 10px; width: 100%; padding: 9px; border-radius: 8px; border: 1px solid #2a3040;
    background: transparent; color: #9aa4b2; font-size: .8rem; font-weight: 600; cursor: pointer;
  }}
  #toast {{
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #1d3a2a; color: #3ddc84; padding: 10px 18px; border-radius: 10px;
    font-size: .85rem; display: none;
  }}
  #equity-wrap {{ position: relative; width: 100%; height: 220px; }}
  #equity-wrap svg {{ width: 100%; height: 100%; display: block; overflow: visible; }}
  .eq-tooltip {{
    position: absolute; pointer-events: none; display: none;
    background: #1c2330; border: 1px solid #2a3040; border-radius: 8px;
    padding: 6px 10px; font-size: .78rem; white-space: nowrap; transform: translate(-50%, -110%);
  }}
  .eq-tooltip .v {{ font-weight: 700; font-size: .88rem; }}
  .eq-tooltip .t {{ color: #7d8896; margin-top: 2px; }}
  .eq-empty {{ color: #5a6472; font-size: .85rem; padding: 30px 4px; text-align: center; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; padding: 7px 0; }}
  .bar-label {{ flex: 0 0 74px; font-size: .78rem; color: #9aa4b2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ flex: 1 1 auto; height: 18px; background: #10141c; border-radius: 4px; overflow: hidden; min-width: 0; }}
  .bar-fill {{ height: 100%; border-radius: 0 4px 4px 0; }}
  .bar-fill.pos {{ background: #3ddc84; }}
  .bar-fill.neg {{ background: #ff6b6b; }}
  .bar-figures {{ flex: 0 0 auto; text-align: right; min-width: 92px; }}
  .bar-value {{ font-size: .82rem; font-weight: 700; }}
  .bar-sub {{ font-size: .68rem; color: #7d8896; }}
  .bar-section-title {{ font-size: .78rem; color: #9aa4b2; margin: 14px 0 4px; }}
  .bar-section-title:first-child {{ margin-top: 0; }}
  .gate-banner {{
    padding: 10px 14px; border-radius: 10px; font-size: .85rem; margin-bottom: 14px;
    display: flex; align-items: center; gap: 8px; line-height: 1.4;
  }}
  .gate-error {{ background: #3a2020; color: #ff6b6b; border: 1px solid #4a2828; }}
  .gate-warn {{ background: #3a2f1d; color: #f0a93d; border: 1px solid #4a3a28; }}
  .gate-lock {{ background: #1e2a3a; color: #6fb3ff; border: 1px solid #28384a; }}
  .hero {{ margin: 4px 0 16px; }}
  .hero-label {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; color: #7d8896; font-weight: 600; }}
  .hero-value {{ font-size: 2.6rem; font-weight: 700; line-height: 1.15; letter-spacing: -0.01em; }}
  .hero-value.pos {{ text-shadow: 0 0 28px rgba(61, 220, 132, .25); }}
  .hero-value.neg {{ text-shadow: 0 0 28px rgba(255, 107, 107, .25); }}
  .hero-delta {{ font-size: .95rem; font-weight: 600; margin-top: 2px; }}
  @media (max-width: 380px) {{ .hero-value {{ font-size: 2.1rem; }} }}
  .sparkline {{ width: 100%; height: 28px; display: block; margin-top: 6px; }}
</style></head>
<body>

<h1>Autonomous Trading Bot
  <span class="pill pill-paper">{mode_label}</span>
  <span class="pill {kill_pill_class}">{kill_label}</span>
  <span class="live-dot" title="Live"></span>
</h1>
<div class="subtitle">Laatst bijgewerkt: automatisch elke 10 sec &middot; ververs handmatig voor de nieuwste stand</div>

<div class="hero">
  <div class="hero-label">Equity</div>
  <div class="hero-value {equity_class}" data-countup="{equity:.2f}" data-format="euro">&euro;{equity:,.2f}</div>
  <div class="hero-delta {pnl_class}">{pnl_sign}&euro;{total_pnl_abs:,.2f} totaal ({pnl_sign}{total_pnl_pct:.1f}%)</div>
</div>
{risk_gate_banner}

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
      <div class="value {pnl_class}">{pnl_sign}<span data-countup="{total_pnl_abs:.2f}" data-format="euro-abs">&euro;{total_pnl_abs:,.2f}</span></div>
      <div class="sub {pnl_class}">{pnl_sign}{total_pnl_pct:.3f}% van startkapitaal</div>
      <svg class="sparkline" id="pnl-sparkline"></svg>
    </div>
    <div class="card">
      <div class="label">Profit factor</div>
      <div class="value">{profit_factor:.2f}</div>
      <div class="sub">Doel: &gt; 1.00</div>
    </div>
  </div>
  <div class="chart-card" style="margin-top: 10px;">
    <div class="chart-title">Equity-curve (cumulatieve winst/verlies, gesloten trades)</div>
    <div id="equity-wrap">
      <div class="eq-empty" id="equity-empty" style="display:none;">Nog geen gesloten trades.</div>
      <div class="eq-tooltip" id="equity-tooltip"></div>
    </div>
  </div>
  <div class="chart-card" style="margin-top: 10px;">
    <div class="bar-section-title">Winst per instrument</div>
    {instrument_bars}
    <div class="bar-section-title">Winst per strategie</div>
    {strategy_bars}
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
  <div class="table-scroll">{positions_table}</div>
</section>

<section>
  <div class="section-title">Live grafieken &amp; exit-plan per positie</div>
  {charts_html}
</section>

<section>
  <div class="section-title">Recent gesloten trades</div>
  <div class="table-scroll">{recent_trades_table}</div>
  <a class="view-all" href="/geschiedenis">Bekijk volledige geschiedenis met datum &amp; tijd &rarr;</a>
</section>

<section>
  <div class="section-title">Signalen &amp; beslissingen</div>
  <a class="view-all" href="/signalen">Bekijk welke signalen recent overwogen/geblokkeerd zijn &rarr;</a>
</section>

<section>
  <div class="section-title">Bediening</div>
  <div class="btn-row">
    <button class="btn-pause" onclick="callControl('/control/pause')">&#9208; Pauzeer</button>
    <button class="btn-resume" onclick="callControl('/control/resume')">&#9654; Hervat</button>
    <button class="btn-flatten" onclick="if(confirm('Alle open posities nu sluiten?')) callControl('/control/flatten')">&#128721; Sluit alles</button>
    <button class="btn-kill" onclick="if(confirm('Noodstop activeren? De bot stopt dan volledig tot een herstart.')) callControl('/control/kill')">&#9888; Noodstop</button>
  </div>
  <div class="note">"Pauzeer" stopt nieuwe trades maar blijft open posities beheren. "Hervat" heft ook een cooldown na 4 verliezen op rij op. "Noodstop" stopt alles hard.</div>
</section>

<div id="toast"></div>
<script>
const _countupFormatters = {{
  euro: v => '€' + v.toLocaleString('nl-NL', {{minimumFractionDigits: 2, maximumFractionDigits: 2}}),
  'euro-abs': v => '€' + Math.abs(v).toLocaleString('nl-NL', {{minimumFractionDigits: 2, maximumFractionDigits: 2}}),
}};

// Numbers count up from 0 to their server-rendered value on each load - a
// full <meta refresh> reload has no previous DOM state to animate FROM, so
// this is a fixed 0->value run each time rather than a true old->new
// transition, but it still gives the "the page just came alive" feel the
// static text alone didn't have. Skipped entirely under
// prefers-reduced-motion.
function animateCountUps() {{
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.querySelectorAll('[data-countup]').forEach(el => {{
    const target = parseFloat(el.dataset.countup);
    const fmt = _countupFormatters[el.dataset.format] || (v => v.toFixed(2));
    if (!isFinite(target)) return;
    const duration = 700;
    const start = performance.now();
    function tick(now) {{
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      el.textContent = fmt(target * eased);
      if (t < 1) requestAnimationFrame(tick);
      else el.textContent = fmt(target);
    }}
    requestAnimationFrame(tick);
  }});
}}
animateCountUps();

async function callControl(path) {{
  const res = await fetch(path, {{ method: 'POST' }});
  const toast = document.getElementById('toast');
  toast.textContent = res.ok ? 'Gelukt' : 'Mislukt';
  toast.style.display = 'block';
  setTimeout(() => toast.style.display = 'none', 1800);
  setTimeout(() => location.reload(), 900);
}}

function drawCandles(canvas, candles, entry, direction) {{
  if (!candles || candles.length === 0) return;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 300;
  const height = 160;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  let lo = Math.min(...candles.map(c => c.l), entry);
  let hi = Math.max(...candles.map(c => c.h), entry);
  const pad = (hi - lo) * 0.08 || Math.abs(entry) * 0.001 || 0.0001;
  lo -= pad; hi += pad;
  const y = v => height - ((v - lo) / (hi - lo)) * height;

  const n = candles.length;
  const cw = width / n;
  candles.forEach((c, i) => {{
    const x = i * cw + cw / 2;
    const up = c.c >= c.o;
    ctx.strokeStyle = up ? '#3ddc84' : '#ff6b6b';
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, y(c.h));
    ctx.lineTo(x, y(c.l));
    ctx.stroke();
    const bodyTop = y(Math.max(c.o, c.c));
    const bodyBot = y(Math.min(c.o, c.c));
    ctx.fillRect(x - cw * 0.32, bodyTop, cw * 0.64, Math.max(1, bodyBot - bodyTop));
  }});

  const entryY = y(entry);
  ctx.strokeStyle = '#f0a93d';
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, entryY);
  ctx.lineTo(width, entryY);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#f0a93d';
  ctx.font = '10px -apple-system, sans-serif';
  ctx.fillText('Instap ' + entry.toFixed(5), 4, entryY > 12 ? entryY - 4 : entryY + 12);
}}

async function loadChart(instrument, entry, direction, canvasId) {{
  try {{
    const res = await fetch(`/api/candles/${{instrument}}?timeframe=M5&count=60`);
    const candles = await res.json();
    const canvas = document.getElementById(canvasId);
    if (canvas) drawCandles(canvas, candles, entry, direction);
  }} catch (e) {{ /* ignore, chart just stays blank */ }}
}}

function fmtEuro(v) {{
  const sign = v >= 0 ? '+' : '-';
  return sign + '€' + Math.abs(v).toLocaleString('nl-NL', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}

function fmtEqDate(iso) {{
  const d = new Date(iso + 'Z');
  return d.toLocaleString('nl-NL', {{day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}});
}}

function svgEl(tag, attrs) {{
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}}

function drawEquityCurve(points) {{
  const wrap = document.getElementById('equity-wrap');
  const tooltip = document.getElementById('equity-tooltip');
  if (!points || points.length < 2) {{
    document.getElementById('equity-empty').style.display = 'block';
    return;
  }}

  const width = wrap.clientWidth || 600;
  const height = 220;
  const padL = 8, padR = 60, padT = 20, padB = 8;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const values = points.map(p => p.cum_pnl);
  let lo = Math.min(0, ...values);
  let hi = Math.max(0, ...values);
  const pad = (hi - lo) * 0.1 || Math.abs(hi) * 0.1 || 1;
  lo -= pad; hi += pad;

  const x = i => padL + (i / (points.length - 1)) * plotW;
  const y = v => padT + (1 - (v - lo) / (hi - lo)) * plotH;
  const zeroY = y(0);

  const svg = svgEl('svg', {{viewBox: `0 0 ${{width}} ${{height}}`, preserveAspectRatio: 'none'}});

  const clipAbove = svgEl('clipPath', {{id: 'eq-clip-above'}});
  clipAbove.appendChild(svgEl('rect', {{x: 0, y: 0, width, height: Math.max(0, zeroY)}}));
  const clipBelow = svgEl('clipPath', {{id: 'eq-clip-below'}});
  clipBelow.appendChild(svgEl('rect', {{x: 0, y: Math.max(0, zeroY), width, height: Math.max(0, height - zeroY)}}));
  const defs = svgEl('defs', {{}});
  defs.appendChild(clipAbove); defs.appendChild(clipBelow);
  svg.appendChild(defs);

  // zero baseline - hairline, solid, recessive
  svg.appendChild(svgEl('line', {{
    x1: padL, y1: zeroY, x2: width - padR, y2: zeroY,
    stroke: '#232a38', 'stroke-width': 1,
  }}));

  let lineD = '';
  points.forEach((p, i) => {{ lineD += (i === 0 ? 'M' : 'L') + x(i) + ',' + y(p.cum_pnl) + ' '; }});
  const areaD = lineD + `L ${{x(points.length - 1)}},${{zeroY}} L ${{x(0)}},${{zeroY}} Z`;

  const areaAbove = svgEl('path', {{d: areaD, fill: '#3ddc84', opacity: 0.10, 'clip-path': 'url(#eq-clip-above)'}});
  const areaBelow = svgEl('path', {{d: areaD, fill: '#ff6b6b', opacity: 0.10, 'clip-path': 'url(#eq-clip-below)'}});
  svg.appendChild(areaAbove); svg.appendChild(areaBelow);

  const linePath = svgEl('path', {{d: lineD.trim(), fill: 'none', stroke: '#6fb3ff', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}});
  svg.appendChild(linePath);
  // Draws itself in left-to-right on load via a dash-offset sweep, instead
  // of just appearing - skipped under prefers-reduced-motion.
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduceMotion) {{
    const len = linePath.getTotalLength();
    linePath.style.strokeDasharray = len;
    linePath.style.strokeDashoffset = len;
    linePath.style.transition = 'stroke-dashoffset 900ms ease-out';
    requestAnimationFrame(() => requestAnimationFrame(() => {{ linePath.style.strokeDashoffset = '0'; }}));
  }}

  // y-axis reference labels: max and min always shown; zero only if it
  // doesn't collide with either (e.g. when the min is close to zero
  // relative to a much larger max, their labels would otherwise overlap -
  // the zero gridline itself still communicates "zero" either way).
  const minLabelGap = 14;
  const maxY = Math.min(height - padB - 2, Math.max(padT + 8, y(hi - pad)));
  const minY = Math.min(height - padB - 2, Math.max(padT + 8, y(lo + pad)));
  const labelPositions = [[hi - pad, maxY], [lo + pad, minY]];
  const zeroY2 = Math.min(height - padB - 2, Math.max(padT + 8, y(0)));
  if (Math.abs(zeroY2 - maxY) >= minLabelGap && Math.abs(zeroY2 - minY) >= minLabelGap) {{
    labelPositions.push([0, zeroY2]);
  }}
  labelPositions.forEach(([v, ty]) => {{
    const label = svgEl('text', {{x: width - padR + 8, y: ty, fill: '#7d8896', 'font-size': 10}});
    label.textContent = fmtEuro(v);
    svg.appendChild(label);
  }});

  // end marker + label (value at the end, per line-chart convention)
  const lastI = points.length - 1;
  const lastV = points[lastI].cum_pnl;
  const endX = x(lastI), endY = y(lastV);
  const ring = svgEl('circle', {{cx: endX, cy: endY, r: 5, fill: '#0b0e14'}});
  const dot = svgEl('circle', {{cx: endX, cy: endY, r: 4, fill: lastV >= 0 ? '#3ddc84' : '#ff6b6b'}});
  svg.appendChild(ring); svg.appendChild(dot);
  const endLabel = svgEl('text', {{
    x: Math.min(endX, width - padR - 4), y: padT - 6, fill: lastV >= 0 ? '#3ddc84' : '#ff6b6b',
    'font-size': 12, 'font-weight': 700, 'text-anchor': 'end',
  }});
  endLabel.textContent = fmtEuro(lastV);
  svg.appendChild(endLabel);

  // crosshair (hidden until hover)
  const crosshair = svgEl('line', {{
    x1: 0, y1: padT, x2: 0, y2: height - padB, stroke: '#7d8896', 'stroke-width': 1, opacity: 0, 'pointer-events': 'none',
  }});
  svg.appendChild(crosshair);

  const hitRect = svgEl('rect', {{x: padL, y: 0, width: plotW, height, fill: 'transparent'}});
  svg.appendChild(hitRect);

  function onMove(evt) {{
    const rect = svg.getBoundingClientRect();
    const scaleX = width / rect.width;
    const px = (evt.clientX - rect.left) * scaleX;
    let idx = Math.round(((px - padL) / plotW) * (points.length - 1));
    idx = Math.max(0, Math.min(points.length - 1, idx));
    const px2 = x(idx);
    crosshair.setAttribute('x1', px2); crosshair.setAttribute('x2', px2);
    crosshair.setAttribute('opacity', 1);
    const scaleXBack = rect.width / width;
    tooltip.style.left = (px2 * scaleXBack) + 'px';
    tooltip.style.top = y(points[idx].cum_pnl) * (rect.height / height) + 'px';
    tooltip.style.display = 'block';
    const valSpan = points[idx].cum_pnl >= 0 ? 'pos' : 'neg';
    tooltip.innerHTML = '';
    const vEl = document.createElement('div'); vEl.className = 'v ' + valSpan;
    vEl.textContent = fmtEuro(points[idx].cum_pnl);
    const tEl = document.createElement('div'); tEl.className = 't';
    tEl.textContent = fmtEqDate(points[idx].t);
    tooltip.appendChild(vEl); tooltip.appendChild(tEl);
  }}
  function onLeave() {{
    crosshair.setAttribute('opacity', 0);
    tooltip.style.display = 'none';
  }}
  hitRect.addEventListener('pointermove', onMove);
  hitRect.addEventListener('pointerleave', onLeave);

  wrap.insertBefore(svg, tooltip);
}}

function drawSparkline(svgId, points) {{
  const svg = document.getElementById(svgId);
  if (!svg || !points || points.length < 2) return;
  const width = svg.clientWidth || 200;
  const height = 28;
  svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);
  svg.innerHTML = '';

  // Sample down to ~12 points - a sparkline shows the shape of the trend
  // at a glance, not the detail (that's what the full chart is for).
  const n = Math.min(points.length, 12);
  const step = (points.length - 1) / (n - 1);
  const sampled = [];
  for (let i = 0; i < n; i++) sampled.push(points[Math.round(i * step)]);
  const values = sampled.map(p => p.cum_pnl);
  const lo = Math.min(...values), hi = Math.max(...values);
  const range = (hi - lo) || 1;
  const x = i => (i / (n - 1)) * width;
  const y = v => height - 2 - ((v - lo) / range) * (height - 4);
  const color = values[values.length - 1] >= 0 ? '#3ddc84' : '#ff6b6b';

  let d = '';
  sampled.forEach((p, i) => {{ d += (i === 0 ? 'M' : 'L') + x(i) + ',' + y(p.cum_pnl) + ' '; }});
  svg.appendChild(svgEl('path', {{d: d.trim(), fill: 'none', stroke: '#4a5468', 'stroke-width': 1.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}}));

  // last segment rides in the accent/status color - "here's where we are now"
  const lastSeg = `M${{x(n - 2)}},${{y(values[n - 2])}} L${{x(n - 1)}},${{y(values[n - 1])}}`;
  svg.appendChild(svgEl('path', {{d: lastSeg, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round'}}));
  svg.appendChild(svgEl('circle', {{cx: x(n - 1), cy: y(values[n - 1]), r: 2.5, fill: color}}));
}}

async function loadEquityCurve() {{
  try {{
    const res = await fetch('/api/equity_curve');
    const points = await res.json();
    drawEquityCurve(points);
    drawSparkline('pnl-sparkline', points);
  }} catch (e) {{ /* ignore, section just stays empty */ }}
}}
loadEquityCurve();

const openPositionsForCharts = {positions_json};

// The whole page reloads every 10s (<meta refresh>), which used to reset
// every chart-toggle back to hidden - annoying since you'd have to
// re-click "Toon grafiek" after every refresh to keep watching a
// position. Persist which INSTRUMENTS (not canvas_ids, which include an
// index that shifts as positions open/close) have their chart open
// across reloads via sessionStorage, and restore them on load.
function getOpenChartInstruments() {{
  try {{ return new Set(JSON.parse(sessionStorage.getItem('openChartInstruments') || '[]')); }}
  catch (e) {{ return new Set(); }}
}}
function saveOpenChartInstruments(set) {{
  sessionStorage.setItem('openChartInstruments', JSON.stringify([...set]));
}}

function toggleChart(canvasId) {{
  const wrap = document.getElementById('wrap-' + canvasId);
  const btn = document.getElementById('btn-' + canvasId);
  const isHidden = wrap.style.display === 'none' || wrap.style.display === '';
  const p = openPositionsForCharts.find(p => p.canvas_id === canvasId);
  const openSet = getOpenChartInstruments();
  if (isHidden) {{
    wrap.style.display = 'block';
    btn.textContent = 'Verberg grafiek';
    if (!wrap.dataset.loaded) {{
      if (p) loadChart(p.instrument, p.entry, p.direction, canvasId);
      wrap.dataset.loaded = '1';
    }}
    if (p) openSet.add(p.instrument);
  }} else {{
    wrap.style.display = 'none';
    btn.textContent = 'Toon grafiek';
    if (p) openSet.delete(p.instrument);
  }}
  saveOpenChartInstruments(openSet);
}}

openPositionsForCharts.forEach(p => {{
  if (getOpenChartInstruments().has(p.instrument)) toggleChart(p.canvas_id);
}});
</script>
</body></html>"""

POSITION_ROW = """<tr>
  <td data-label="Instrument">{instrument}</td>
  <td data-label="Richting" class="dir-{direction_class}">{direction_label}</td>
  <td data-label="Instap">{entry_price:.5f}</td>
  <td data-label="Nu">{current_price:.5f}</td>
  <td data-label="Winst/Verlies" class="{pnl_class}">{pnl_sign}&euro;{pnl:,.2f}</td>
</tr>"""

TRADE_ROW = """<tr>
  <td data-label="Instrument">{instrument}</td>
  <td data-label="Richting" class="dir-{direction_class}">{direction_label}</td>
  <td data-label="Strategie">{strategy}</td>
  <td data-label="Resultaat" class="{pnl_class}">{pnl_sign}&euro;{pnl:,.2f}</td>
  <td data-label="Reden">{exit_reason}</td>
  <td data-label="Gesloten" class="dimmed">{closed_at}</td>
</tr>"""

HISTORY_ROW = """<tr>
  <td data-label="Geopend" class="dimmed">{opened_at}</td>
  <td data-label="Gesloten" class="dimmed">{closed_at}</td>
  <td data-label="Instrument">{instrument}</td>
  <td data-label="Richting" class="dir-{direction_class}">{direction_label}</td>
  <td data-label="Strategie">{strategy}</td>
  <td data-label="Instap">{entry_price:.5f}</td>
  <td data-label="Uitstap">{exit_price:.5f}</td>
  <td data-label="Resultaat" class="{pnl_class}">{pnl_sign}&euro;{pnl:,.2f}</td>
  <td data-label="R">{r_multiple:+.2f}R</td>
  <td data-label="Reden">{exit_reason}</td>
</tr>"""

HISTORY_TEMPLATE = """<!doctype html>
<html><head><title>Handelsgeschiedenis</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ overflow-x: hidden; width: 100%; }}
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    background: #0b0e14; color: #e8ebf0; margin: 0;
    padding: 16px 16px 48px; max-width: 1000px; margin-inline: auto;
  }}
  h1 {{ font-size: 1.15rem; font-weight: 600; margin: 4px 0 2px; }}
  .subtitle {{ color: #7d8896; font-size: .85rem; margin-bottom: 18px; }}
  a {{ color: #6fb3ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .back {{ display: inline-block; margin-bottom: 14px; font-size: .85rem; }}
  .table-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 10px; max-width: 100%; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .82rem; white-space: nowrap; }}
  @media (max-width: 640px) {{
    .table-scroll table, .table-scroll thead, .table-scroll tbody,
    .table-scroll th, .table-scroll td, .table-scroll tr {{ display: block; }}
    .table-scroll thead {{ display: none; }}
    .table-scroll tr {{
      border: 1px solid #232a38; border-radius: 10px; padding: 4px 10px;
      margin-bottom: 8px; background: #10141c;
    }}
    .table-scroll td {{
      display: flex; justify-content: space-between; align-items: center;
      white-space: normal; border-bottom: 1px solid #1c2330; padding: 7px 0;
      text-align: right;
    }}
    .table-scroll td:last-child {{ border-bottom: none; }}
    .table-scroll td::before {{
      content: attr(data-label); color: #7d8896; font-weight: 500;
      text-align: left; padding-right: 10px;
    }}
  }}
  th {{ text-align: left; color: #7d8896; font-weight: 500; padding: 8px 10px; border-bottom: 1px solid #232a38; position: sticky; top: 0; background: #0b0e14; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #1c2330; }}
  .dimmed {{ color: #7d8896; }}
  .empty {{ color: #5a6472; font-size: .85rem; padding: 10px 4px; }}
  .pos {{ color: #3ddc84; }} .neg {{ color: #ff6b6b; }}
  .dir-long {{ color: #3ddc84; font-weight: 600; }}
  .dir-short {{ color: #ff6b6b; font-weight: 600; }}
  .card {{
    background: #151a24; border: 1px solid #232a38; border-radius: 14px;
    padding: 14px; margin-bottom: 16px; font-size: .85rem; color: #9aa4b2;
  }}
</style></head>
<body>
<a class="back" href="/">&larr; Terug naar dashboard</a>
<h1>Volledige handelsgeschiedenis</h1>
<div class="subtitle">Alle gesloten trades, nieuwste eerst &middot; {count} trades totaal</div>
<div class="card">Totale winst/verlies: <span class="{total_pnl_class}">{total_pnl_sign}&euro;{total_pnl_abs:,.2f}</span> &middot; Win rate: {win_rate:.1f}% &middot; Profit factor: {profit_factor:.2f}</div>
<div class="table-scroll">
<table>
<tr>
  <th>Geopend</th><th>Gesloten</th><th>Instrument</th><th>Richting</th><th>Strategie</th>
  <th>Instap</th><th>Uitstap</th><th>Resultaat</th><th>R</th><th>Reden</th>
</tr>
{rows}
</table>
</div>
</body></html>"""

SIGNALS_TEMPLATE = """<!doctype html>
<html><head><title>Signalen &amp; beslissingen</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>
  // A plain <meta refresh> reloads the whole page and resets scroll to the
  // top every 15s — scrolling down to read older rows got yanked back up
  // before you could finish reading them. Save/restore scroll position
  // around the reload instead of losing it every cycle.
  window.addEventListener('beforeunload', function () {{
    sessionStorage.setItem('signalenScrollY', window.scrollY);
  }});
  window.addEventListener('DOMContentLoaded', function () {{
    var y = sessionStorage.getItem('signalenScrollY');
    if (y) window.scrollTo(0, parseInt(y, 10));
  }});
  setTimeout(function () {{ location.reload(); }}, 15000);
</script>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ overflow-x: hidden; width: 100%; }}
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    background: #0b0e14; color: #e8ebf0; margin: 0;
    padding: 16px 16px 48px; max-width: 1000px; margin-inline: auto;
  }}
  h1 {{ font-size: 1.15rem; font-weight: 600; margin: 4px 0 2px; }}
  .subtitle {{ color: #7d8896; font-size: .85rem; margin-bottom: 18px; }}
  a {{ color: #6fb3ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .back {{ display: inline-block; margin-bottom: 14px; font-size: .85rem; }}
  .table-scroll {{ overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 10px; max-width: 100%; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
  @media (max-width: 640px) {{
    .table-scroll table, .table-scroll thead, .table-scroll tbody,
    .table-scroll th, .table-scroll td, .table-scroll tr {{ display: block; }}
    .table-scroll thead {{ display: none; }}
    .table-scroll tr {{
      border: 1px solid #232a38; border-radius: 10px; padding: 4px 10px;
      margin-bottom: 8px; background: #10141c;
    }}
    .table-scroll td {{
      display: flex; justify-content: space-between; align-items: flex-start;
      white-space: normal; border-bottom: 1px solid #1c2330; padding: 7px 0;
      text-align: right; gap: 10px;
    }}
    .table-scroll td:last-child {{ border-bottom: none; }}
    .table-scroll td::before {{
      content: attr(data-label); color: #7d8896; font-weight: 500;
      text-align: left; padding-right: 10px; flex-shrink: 0;
    }}
  }}
  th {{ text-align: left; color: #7d8896; font-weight: 500; padding: 8px 10px; border-bottom: 1px solid #232a38; position: sticky; top: 0; background: #0b0e14; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #1c2330; white-space: normal; word-break: break-word; }}
  .empty {{ color: #5a6472; font-size: .85rem; padding: 10px 4px; }}
  .tag {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: .72rem; font-weight: 600; white-space: nowrap; word-break: keep-all; }}
  .tag-considered {{ background: #1e2a3a; color: #6fb3ff; }}
  .tag-blocked {{ background: #2a2020; color: #ff6b6b; }}
  .tag-risk {{ background: #1d3a2a; color: #3ddc84; }}
  .tag-mgmt {{ background: #2a2620; color: #e0b84c; }}
  .tag-other {{ background: #232a38; color: #9aa4b2; }}
</style></head>
<body>
<a class="back" href="/">&larr; Terug naar dashboard</a>
<h1>Recente signalen &amp; beslissingen</h1>
<div class="subtitle">Laatste {count} regels &middot; ververst elke 15 sec &middot; laat zien welk signaal overwogen of geblokkeerd werd, en waarom</div>
<div class="table-scroll">
<table>
<tr><th>Tijd</th><th>Instrument</th><th>Type</th><th>Details</th></tr>
{rows}
</table>
</div>
</body></html>"""

CHART_CARD = """<div class="chart-card">
  <div class="chart-title">{instrument} <span class="dir-{direction_class}">{direction_label}</span> &middot; instap {entry_price:.5f}</div>
  <div class="chart-meta">
    <b>Strategie:</b> {strategy}<br>
    <b>Huidig niveau:</b> {r_multiple:+.2f}R ({pnl_sign}&euro;{pnl:,.2f})<br>
    <b>Volgende stap:</b> <span class="next-step">{next_step}</span><br>
    <b>Uiterlijk gesloten over:</b> {time_left}
  </div>
  <button id="btn-{canvas_id}" class="chart-toggle" onclick="toggleChart('{canvas_id}')">Toon grafiek</button>
  <div id="wrap-{canvas_id}" style="display:none; margin-top:8px;">
    <canvas class="candles" id="{canvas_id}"></canvas>
  </div>
</div>"""

BAR_ROW = """<div class="bar-row">
  <div class="bar-label">{name}</div>
  <div class="bar-track"><div class="bar-fill {pnl_class}" style="width: {pct:.1f}%;"></div></div>
  <div class="bar-figures">
    <div class="bar-value {pnl_class}">{pnl_sign}&euro;{pnl_abs:,.2f}</div>
    <div class="bar-sub">{trades} trades &middot; {win_rate:.0f}% win rate</div>
  </div>
</div>"""


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value).replace(tzinfo=UTC)
        return dt.astimezone(_LOCAL_TZ).strftime("%d-%m %H:%M")
    except ValueError:
        return value


def _direction_label(direction_value: str) -> tuple[str, str]:
    if direction_value == Direction.LONG.value:
        return "long", "Long"
    return "short", "Short"


def _active_risk_gate(controller: AutonomousTradingController, equity: float) -> dict | None:
    # Mirrors risk_manager.evaluate()'s account-wide checks (same order,
    # same thresholds) purely to report current status - never blocks
    # anything itself. Added because Isaak had to ask multiple times
    # today whether a gate was actually active; this puts the answer on
    # the dashboard instead.
    state = controller.state
    cfg = controller.cfg.risk
    now = datetime.utcnow()

    if state.kill_switch:
        return {"icon": "\U0001F6A8", "text": "NOODSTOP ACTIEF", "cls": "gate-error"}

    if state.cooldown_until and now < state.cooldown_until:
        until = state.cooldown_until.strftime("%H:%M")
        return {"icon": "⏸", "text": f"Cooldown na verliezen op rij, actief tot {until} UTC", "cls": "gate-warn"}

    month_dd = (state.peak_equity - equity) / state.peak_equity if state.peak_equity else 0.0
    if month_dd >= cfg.max_monthly_drawdown_pct:
        return {"icon": "\U0001F6A8", "text": f"Maandelijkse drawdown-limiet geraakt ({month_dd:.1%})", "cls": "gate-error"}

    day_pnl_pct = (equity - state.day_start_equity) / state.day_start_equity if state.day_start_equity else 0.0
    if day_pnl_pct <= -cfg.max_daily_loss_pct:
        return {
            "icon": "\U0001F512",
            "text": f"Dagverlieslimiet geraakt ({day_pnl_pct:.1%}) — alleen confidence ≥{cfg.daily_loss_limit_min_confidence:.0f} nog toegestaan",
            "cls": "gate-warn",
        }

    week_pnl_pct = (equity - state.week_start_equity) / state.week_start_equity if state.week_start_equity else 0.0
    if week_pnl_pct <= -cfg.max_weekly_loss_pct:
        return {"icon": "\U0001F512", "text": f"Weekverlieslimiet geraakt ({week_pnl_pct:.1%})", "cls": "gate-error"}

    day_peak_gain = (state.day_peak_equity - state.day_start_equity) / state.day_start_equity if state.day_start_equity else 0.0
    if day_peak_gain > 0:
        giveback = (state.day_peak_equity - equity) / (state.day_peak_equity - state.day_start_equity)
        if giveback >= cfg.daily_profit_giveback_pct and day_peak_gain >= cfg.daily_profit_soft_lock_pct:
            return {"icon": "\U0001F512", "text": f"Te veel van dagwinst teruggegeven ({giveback:.0%}) — dag geblokkeerd", "cls": "gate-warn"}

    if day_pnl_pct >= cfg.daily_profit_lock_pct:
        return {
            "icon": "\U0001F3AF",
            "text": f"Dagdoel gehaald ({day_pnl_pct:.1%}) — alleen confidence ≥{cfg.daily_profit_lock_min_confidence:.0f} nog toegestaan",
            "cls": "gate-lock",
        }

    return None


def _next_step_text(r_multiple: float, breakeven_moved: bool, trailing_active: bool) -> str:
    if r_multiple < 1.0:
        return f"Break-even (stop naar instap) bij 1R &mdash; nu op {r_multiple:.2f}R"
    if r_multiple < 1.5:
        return "30% van de positie wordt verkocht bij 1,5R winst"
    if r_multiple < 2.0:
        return "Nog eens 30% wordt verkocht bij 2R winst"
    return "Restant volgt een meebewegende (trailing) stop-loss"


def _bar_rows(stats: dict[str, dict], limit: int = 8) -> str:
    # Bars grow from a single baseline (the left edge), length = magnitude
    # relative to the biggest bar in this set, color = sign - a diverging
    # bar chart without needing an actual zero-centered axis, which would
    # need much more layout work for the same information.
    if not stats:
        return '<div class="empty">Nog geen data.</div>'
    items = sorted(stats.items(), key=lambda kv: kv[1]["total_pnl"], reverse=True)[:limit]
    max_abs = max((abs(v["total_pnl"]) for _, v in items), default=0.0) or 1.0
    rows = []
    for name, v in items:
        pnl = v["total_pnl"]
        rows.append(BAR_ROW.format(
            name=name, pnl_class="pos" if pnl >= 0 else "neg",
            pct=max(2.0, abs(pnl) / max_abs * 100),
            pnl_sign="+" if pnl >= 0 else "-", pnl_abs=abs(pnl),
            trades=v["trades"], win_rate=v["win_rate"] * 100,
        ))
    return "".join(rows)


def create_app(controller: AutonomousTradingController) -> FastAPI:
    app = FastAPI(title="Autonomous Trading Bot Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def home():
        account = await controller.broker.get_account_info()
        positions = await controller.broker.get_open_positions()
        perf = compute_performance(controller.db)
        starting_balance = controller.cfg.starting_balance
        max_hold = controller.position_manager.max_hold

        open_trade_meta = {row["id"]: row for row in controller.db.fetch_open_trades()}

        gate = _active_risk_gate(controller, account.equity)
        risk_gate_banner = (
            f'<div class="gate-banner {gate["cls"]}"><span>{gate["icon"]}</span><span>{gate["text"]}</span></div>'
            if gate else ""
        )

        equity_change_pct = ((account.equity - account.balance) / account.balance * 100) if account.balance else 0.0
        total_pnl_pct = (perf.total_pnl / starting_balance * 100) if starting_balance else 0.0

        rows = []
        chart_cards = []
        positions_for_js = []
        for idx, pos in enumerate(positions):
            try:
                quote = await controller.broker.get_quote(pos.instrument)
                current_price = quote.mid
            except Exception:
                current_price = pos.entry_price
            direction_mult = 1 if pos.direction == Direction.LONG else -1
            # Prefer the broker's own authoritative P&L (accounts for
            # contract size, e.g. 100 oz/lot on XAUUSD) over our manual
            # estimate, which understated it by that same factor since
            # `quantity` here is the broker's lot size, not underlying units.
            if pos.broker_unrealized_pnl is not None:
                pnl = pos.broker_unrealized_pnl
            else:
                pnl = (current_price - pos.entry_price) * direction_mult * pos.quantity
            dclass, dlabel = _direction_label(pos.direction.value)
            rows.append(POSITION_ROW.format(
                instrument=pos.instrument, direction_class=dclass, direction_label=dlabel,
                entry_price=pos.entry_price, current_price=current_price,
                pnl_class="pos" if pnl >= 0 else "neg", pnl_sign="+" if pnl >= 0 else "-", pnl=abs(pnl),
            ))

            risk_per_unit = abs(pos.entry_price - pos.initial_stop_loss)
            r_multiple = ((current_price - pos.entry_price) * direction_mult / risk_per_unit) if risk_per_unit else 0.0
            next_step = _next_step_text(r_multiple, pos.breakeven_moved, pos.trailing_active)

            meta_row = open_trade_meta.get(pos.id)
            strategy_label = meta_row["strategy"] if meta_row else "onbekend"
            opened_at_str = meta_row["opened_at"] if meta_row else None
            time_left = "onbekend"
            if opened_at_str:
                try:
                    opened_at = datetime.fromisoformat(opened_at_str)
                    elapsed = datetime.utcnow() - opened_at
                    remaining = max_hold - elapsed
                    if remaining.total_seconds() > 0:
                        mins = int(remaining.total_seconds() // 60)
                        time_left = f"{mins} min (tijdslimiet)"
                    else:
                        time_left = "nu (tijdslimiet bereikt)"
                except ValueError:
                    pass

            canvas_id = f"chart-{idx}-{pos.instrument}"
            chart_cards.append(CHART_CARD.format(
                instrument=pos.instrument, direction_class=dclass, direction_label=dlabel,
                entry_price=pos.entry_price, canvas_id=canvas_id,
                strategy=strategy_label, r_multiple=r_multiple,
                pnl_sign="+" if pnl >= 0 else "-", pnl=abs(pnl),
                next_step=next_step, time_left=time_left,
            ))
            positions_for_js.append({
                "instrument": pos.instrument, "entry": pos.entry_price,
                "direction": dclass, "canvas_id": canvas_id,
            })
        positions_table = (
            "<table><tr><th>Instrument</th><th>Richting</th><th>Instap</th><th>Nu</th><th>Winst/Verlies</th></tr>"
            + "".join(rows) + "</table>"
        ) if rows else '<div class="empty">Geen open posities op dit moment.</div>'
        charts_html = "".join(chart_cards) if chart_cards else '<div class="empty">Geen open posities om te tonen.</div>'

        closed = [
            r for r in controller.db.fetch_closed_trades()
            if r["exit_reason"] != "reconciliation_broker_missing"
        ][:10]
        trade_rows = []
        for row in closed:
            pnl = row["pnl"] or 0.0
            dclass, dlabel = _direction_label(row["direction"])
            trade_rows.append(TRADE_ROW.format(
                instrument=row["instrument"], direction_class=dclass, direction_label=dlabel,
                strategy=row["strategy"], pnl_class="pos" if pnl >= 0 else "neg",
                pnl_sign="+" if pnl >= 0 else "-", pnl=abs(pnl),
                exit_reason=row["exit_reason"] or "-",
                closed_at=_fmt_dt(row["closed_at"]),
            ))
        recent_trades_table = (
            "<table><tr><th>Instrument</th><th>Richting</th><th>Strategie</th><th>Resultaat</th><th>Reden</th><th>Gesloten</th></tr>"
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
            charts_html=charts_html,
            recent_trades_table=recent_trades_table,
            positions_json=json.dumps(positions_for_js),
            instrument_bars=_bar_rows(perf.by_instrument),
            strategy_bars=_bar_rows(perf.by_strategy),
            risk_gate_banner=risk_gate_banner,
        )

    @app.get("/geschiedenis", response_class=HTMLResponse)
    async def history():
        perf = compute_performance(controller.db)
        all_closed = [
            r for r in controller.db.fetch_closed_trades()
            if r["exit_reason"] != "reconciliation_broker_missing"
        ]

        rows = []
        for row in all_closed:
            pnl = row["pnl"] or 0.0
            dclass, dlabel = _direction_label(row["direction"])
            rows.append(HISTORY_ROW.format(
                opened_at=_fmt_dt(row["opened_at"]),
                closed_at=_fmt_dt(row["closed_at"]),
                instrument=row["instrument"], direction_class=dclass, direction_label=dlabel,
                strategy=row["strategy"], entry_price=row["entry_price"] or 0.0,
                exit_price=row["exit_price"] or 0.0,
                pnl_class="pos" if pnl >= 0 else "neg",
                pnl_sign="+" if pnl >= 0 else "-", pnl=abs(pnl),
                r_multiple=row["r_multiple"] or 0.0,
                exit_reason=row["exit_reason"] or "-",
            ))

        return HISTORY_TEMPLATE.format(
            count=len(all_closed),
            rows="".join(rows) if rows else '<tr><td colspan="10" class="empty">Nog geen trades afgesloten.</td></tr>',
            total_pnl_class="pos" if perf.total_pnl >= 0 else "neg",
            total_pnl_sign="+" if perf.total_pnl >= 0 else "-",
            total_pnl_abs=abs(perf.total_pnl),
            win_rate=perf.win_rate * 100,
            profit_factor=perf.profit_factor,
        )

    @app.get("/signalen", response_class=HTMLResponse)
    async def signals():
        # At ~15-20 decision rows per 15s scan cycle across 3 instruments, a
        # limit of 60 covered under a minute of history — not enough to
        # actually scroll through, even with scroll position now preserved
        # across the auto-refresh.
        decisions = controller.db.fetch_recent_decisions(limit=500)

        tag_map = {
            "signal_considered": ("tag-considered", "Overwogen"),
            "signal_filtered": ("tag-blocked", "Uitgefilterd"),
            "scan_skipped": ("tag-other", "Overgeslagen"),
            "trade_blocked": ("tag-blocked", "Geblokkeerd"),
            "risk_decision": ("tag-risk", "Risk-check"),
            "position_management": ("tag-mgmt", "Beheer"),
        }

        skip_reason_labels = {
            "news_blackout": "nieuws-blackout",
            "broker_data_error": "broker gaf geen data",
            "insufficient_bars": "te weinig candles",
            "regime_not_tradeable": "regime niet geschikt om te traden",
            "no_strategy_signal": "geen enkele strategie gaf een signaal",
            "cost_too_high": "kosten te hoog t.o.v. verwachte winst",
            "opportunity_score_too_low": "opportunity-score te laag",
        }

        filter_reason_labels = {
            "confidence_below_threshold": "confidence te laag",
            "strategy_disabled_by_optimizer": "strategie tijdelijk uitgeschakeld",
            "strategy_permanently_disabled": "strategie permanent uitgeschakeld",
            "strategy_not_allowed_for_instrument": "strategie niet toegestaan voor dit instrument",
        }

        rows = []
        for row in decisions:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}

            dtype = row["decision_type"]
            tag_class, tag_label = tag_map.get(dtype, ("tag-other", dtype))

            if dtype == "signal_considered":
                detail = (
                    f"confidence {payload.get('confidence', '?')}, "
                    f"score {payload.get('opportunity_score', '?'):.1f}" if isinstance(payload.get("opportunity_score"), (int, float))
                    else f"confidence {payload.get('confidence', '?')}"
                )
            elif dtype == "signal_filtered":
                reason = payload.get("reason", "?")
                label = filter_reason_labels.get(reason, reason)
                strat = payload.get("strategy", "?")
                detail = f"{strat}: {label}"
                if reason == "confidence_below_threshold":
                    detail += f" ({payload.get('confidence', '?')} &lt; {payload.get('min_confidence_required', '?')})"
                if reason == "strategy_not_allowed_for_instrument":
                    detail += f" &mdash; toegestaan: {', '.join(payload.get('allowed', []))}"
            elif dtype == "scan_skipped":
                reason = payload.get("reason", "?")
                label = skip_reason_labels.get(reason, reason)
                detail = label
                if reason == "regime_not_tradeable":
                    detail += f" ({payload.get('regime', '?')})"
                if reason == "no_strategy_signal":
                    detail += f" (regime: {payload.get('regime', '?')})"
                if reason == "opportunity_score_too_low":
                    detail += f" &mdash; {payload.get('strategy', '?')}: {payload.get('score', '?')} &lt; {payload.get('min_required', '?')}"
                if reason == "cost_too_high":
                    detail += f" &mdash; {payload.get('strategy', '?')}: {payload.get('cost_ratio', 0)*100:.0f}%"
            elif dtype == "trade_blocked":
                detail = f"reden: {payload.get('reason', '?')}"
            elif dtype == "risk_decision":
                if payload.get("blocked"):
                    detail = f"geblokkeerd &mdash; {payload.get('block_reason', '?')}"
                else:
                    detail = f"risk {payload.get('risk_pct', 0)*100:.3f}%"
            elif dtype == "position_management":
                detail = f"{payload.get('action', '?')} &mdash; {payload.get('detail', '')}"
            else:
                detail = json.dumps(payload)[:120]

            rows.append(
                f'<tr><td data-label="Tijd">{_fmt_dt(row["ts"])}</td>'
                f'<td data-label="Instrument">{row["instrument"] or "-"}</td>'
                f'<td data-label="Type"><span class="tag {tag_class}">{tag_label}</span></td>'
                f'<td data-label="Details">{detail}</td></tr>'
            )

        return SIGNALS_TEMPLATE.format(
            count=len(decisions),
            rows="".join(rows) if rows else '<tr><td colspan="4" class="empty">Nog geen signalen geregistreerd.</td></tr>',
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

    @app.get("/api/equity_curve")
    async def equity_curve_api():
        return equity_curve(controller.db)

    @app.get("/api/candles/{instrument}")
    async def candles(instrument: str, timeframe: str = "M5", count: int = 60):
        data = await controller.broker.get_candles(instrument, timeframe, count)
        return [
            {"t": c.time.isoformat(), "o": c.open, "h": c.high, "l": c.low, "c": c.close}
            for c in data
        ]

    @app.post("/control/pause")
    async def pause():
        controller.paused = True
        return {"paused": True}

    @app.post("/control/resume")
    async def resume():
        controller.paused = False
        # "Resume" must also act as a manual override of the consecutive-
        # loss cooldown — otherwise pressing it after 4 losses in a row did
        # nothing, since that block came from the risk manager, not the
        # pause flag, and only an automatic cooldown timer or a win cleared
        # it. A human explicitly pressing resume is a deliberate override.
        controller.state.consecutive_losses = 0
        controller.state.cooldown_until = None
        controller._persist_state()
        return {"paused": False}

    @app.post("/control/kill")
    async def kill():
        controller.safety.manual_kill_switch(controller.state, "dashboard_manual")
        controller._persist_state()
        return {"kill_switch": True}

    @app.post("/control/flatten")
    async def flatten():
        positions = await controller.broker.get_open_positions()
        await controller.position_manager.emergency_close_all(positions, "manual_flatten")
        return {"closed": len(positions)}

    return app
