"""Interactive Discord bot - lets Isaak query and control the trading bot
from Discord instead of only the read-only-mostly dashboard. Runs as a
background asyncio task alongside the controller and dashboard server
(see run_paper.py); optional (disabled automatically if not configured).

Every command is restricted to DISCORD_OWNER_ID - these can pause, kill,
or flatten a live-money-adjacent trading bot, so anyone else in the
Discord server must be refused, not just discouraged.
"""
from __future__ import annotations

import json
import os

import structlog

from tradingbot.controller import AutonomousTradingController
from tradingbot.notifications import CATEGORY_WEBHOOKS_PATH, load_category_webhooks
from tradingbot.performance import compute_performance
from tradingbot.risk_status import active_risk_gate

log = structlog.get_logger(__name__)

# category -> channel name. Split by concern so a "just checking the
# account" glance (trades) doesn't get buried under noisy risk alerts, and
# vice versa - Isaak's request 2026-08-06.
_CHANNEL_PLAN = {
    "trades": "trades",
    "samenvattingen": "dag-en-week-overzicht",
    "risico": "risico-meldingen",
}
_WEBHOOK_NAME = "Tradez"


async def _ensure_channels_and_webhooks(guild) -> dict[str, str]:
    import discord

    urls: dict[str, str] = load_category_webhooks()
    changed = False
    for category, channel_name in _CHANNEL_PLAN.items():
        if urls.get(category):
            continue  # already provisioned in a previous run - idempotent
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel is None:
            try:
                channel = await guild.create_text_channel(channel_name)
                log.info("discord_bot.channel_created", name=channel_name)
            except discord.Forbidden:
                log.warning(
                    "discord_bot.missing_permission",
                    detail=f"Cannot create #{channel_name} - grant 'Manage Channels' to the bot's role",
                )
                continue
        try:
            existing = await channel.webhooks()
            webhook = next((w for w in existing if w.name == _WEBHOOK_NAME), None)
            if webhook is None:
                webhook = await channel.create_webhook(name=_WEBHOOK_NAME)
            urls[category] = webhook.url
            changed = True
        except discord.Forbidden:
            log.warning(
                "discord_bot.missing_permission",
                detail=f"Cannot manage webhooks in #{channel_name} - grant 'Manage Webhooks' to the bot's role",
            )

    if changed:
        CATEGORY_WEBHOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATEGORY_WEBHOOKS_PATH.write_text(json.dumps(urls, indent=2))
    return urls


async def run_discord_bot(controller: AutonomousTradingController) -> None:
    # A problem anywhere in this optional integration (bad token, discord.py
    # bug, network flakiness) must never be able to take down the live
    # trading loop or dashboard it runs alongside - so the entire body is
    # wrapped and any exception is swallowed after logging, never re-raised
    # into the asyncio.gather() in run_paper.py.
    try:
        await _run(controller)
    except Exception as exc:  # noqa: BLE001
        log.error("discord_bot.crashed", error=str(exc))


async def _run(controller: AutonomousTradingController) -> None:
    import discord
    from discord import app_commands

    token = os.environ.get("DISCORD_BOT_TOKEN")
    owner_id_raw = os.environ.get("DISCORD_OWNER_ID")
    if not token or not owner_id_raw:
        log.info("discord_bot.not_configured", detail="DISCORD_BOT_TOKEN/DISCORD_OWNER_ID not set - interactive bot disabled")
        return
    owner_id = int(owner_id_raw)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    def owner_only(interaction: "discord.Interaction") -> bool:
        return interaction.user.id == owner_id

    async def refuse(interaction: "discord.Interaction") -> None:
        await interaction.response.send_message("Niet geautoriseerd.", ephemeral=True)

    @tree.command(name="status", description="Status van de trading bot")
    async def status_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        account = await controller.broker.get_account_info()
        positions = await controller.broker.get_open_positions()
        gate = active_risk_gate(controller.state, controller.cfg.risk, account.equity)
        lines = [
            f"**Modus:** {controller.cfg.mode}{' (GEPAUZEERD)' if controller.paused else ''}",
            f"**Noodstop:** {'JA' if controller.state.kill_switch else 'nee'}",
            f"**Equity:** €{account.equity:,.2f}",
            f"**Open posities:** {len(positions)}",
        ]
        if gate:
            lines.append(f"**Actieve regel:** {gate['icon']} {gate['text']}")
        await interaction.response.send_message("\n".join(lines))

    @tree.command(name="posities", description="Toon open posities")
    async def posities_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        positions = await controller.broker.get_open_positions()
        if not positions:
            await interaction.response.send_message("Geen open posities.")
            return
        lines = []
        for p in positions:
            try:
                quote = await controller.broker.get_quote(p.instrument)
                current = quote.mid
            except Exception:  # noqa: BLE001
                current = p.entry_price
            direction_mult = 1 if p.direction.value == "long" else -1
            pnl = p.broker_unrealized_pnl if p.broker_unrealized_pnl is not None else (current - p.entry_price) * direction_mult * p.quantity
            lines.append(f"{p.instrument} {p.direction.value} — {pnl:+.2f} EUR")
        await interaction.response.send_message("\n".join(lines))

    @tree.command(name="prestaties", description="Prestatie-overzicht (alle gesloten trades)")
    async def prestaties_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        perf = compute_performance(controller.db)
        pf = "inf" if perf.profit_factor == float("inf") else f"{perf.profit_factor:.2f}"
        await interaction.response.send_message(
            f"**Trades:** {perf.total_trades}\n"
            f"**Win rate:** {perf.win_rate * 100:.1f}%\n"
            f"**Winst/verlies:** {perf.total_pnl:+.2f} EUR\n"
            f"**Profit factor:** {pf}"
        )

    @tree.command(name="pauzeer", description="Stop nieuwe trades (open posities blijven beheerd)")
    async def pauzeer_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        controller.paused = True
        await interaction.response.send_message("⏸ Bot gepauzeerd. Nieuwe trades gestopt, open posities blijven beheerd.")

    @tree.command(name="hervat", description="Hef pauze en cooldown na verliezen op")
    async def hervat_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        controller.paused = False
        controller.state.consecutive_losses = 0
        controller.state.cooldown_until = None
        controller._persist_state()
        await interaction.response.send_message("▶️ Bot hervat.")

    @tree.command(name="noodstop", description="NOODSTOP: stopt de bot volledig tot een herstart")
    async def noodstop_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        controller.safety.manual_kill_switch(controller.state, "discord_manual")
        controller._persist_state()
        await interaction.response.send_message("🚨 NOODSTOP geactiveerd. Bot stopt met traden tot een herstart.")

    @tree.command(name="sluit_alles", description="Sluit alle open posities direct")
    async def sluit_alles_cmd(interaction: "discord.Interaction"):
        if not owner_only(interaction):
            return await refuse(interaction)
        await interaction.response.defer()
        positions = await controller.broker.get_open_positions()
        await controller.position_manager.emergency_close_all(positions, "manual_flatten_discord")
        await interaction.followup.send(f"🛑 {len(positions)} positie(s) gesloten.")

    @client.event
    async def on_ready():
        # Syncing per-guild (instead of only global, which can take up to an
        # hour to propagate) makes the slash commands usable immediately in
        # whichever server(s) the bot has been added to.
        for guild in client.guilds:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)

        if client.guilds:
            # Single-owner bot in one server - the first (only) guild is
            # where the dedicated channels/webhooks live.
            urls = await _ensure_channels_and_webhooks(client.guilds[0])
            if urls:
                controller.notifications.set_category_webhooks(urls)
                log.info("discord_bot.category_webhooks_ready", categories=list(urls.keys()))

        log.info("discord_bot.ready", user=str(client.user), guilds=[g.name for g in client.guilds])

    await client.start(token)
