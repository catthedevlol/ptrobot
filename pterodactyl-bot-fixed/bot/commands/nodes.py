"""
commands/nodes.py — Node management slash commands.

Fixes applied:
- /node-stat and /node-uptime now use the Wings-aware get_node_stats() which
  correctly reflects actual online/offline state.
- Initial embed posted by /node-stat reflects real online state (not just
  API reachability).
- All API errors show clean embeds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import (
    require_any_admin,
    success_embed,
    error_embed,
    fmt_bytes,
    progress_bar,
    GREEN,
    GOLD,
    RED,
)
from utils.ptero_api import PterodactylAPI, PterodactylError, NodeStats
from views.persistent import build_node_view

log = logging.getLogger("bot.cmd.nodes")


class NodeCommands(commands.Cog, name="Nodes"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _api(self) -> PterodactylAPI:
        return PterodactylAPI(
            self.bot.config.PANEL_URL,
            self.bot.config.PANEL_API_KEY,
        )

    # ── /node-stat ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="node-stat",
        description="Post a live-updating node status embed in a channel.",
    )
    @app_commands.describe(
        channel="The channel to post the status embed in.",
        node_id="Pterodactyl node ID (leave blank to post all nodes).",
    )
    @require_any_admin()
    async def node_stat(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        node_id: int | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api = self._api()
        db = self.bot.db

        try:
            nodes = await api.list_nodes()
        except PterodactylError as exc:
            await interaction.followup.send(
                embed=error_embed("API Error", f"Could not fetch nodes: {exc}"),
                ephemeral=True,
            )
            await api.close()
            return

        if node_id is not None:
            nodes = [n for n in nodes if n["id"] == node_id]
            if not nodes:
                await interaction.followup.send(
                    embed=error_embed("Not Found", f"No node with ID {node_id}."),
                    ephemeral=True,
                )
                await api.close()
                return

        posted = 0
        for node in nodes:
            nid = node["id"]
            try:
                stats = await api.get_node_stats(nid)
            except PterodactylError as exc:
                log.warning("Could not get stats for node %d: %s", nid, exc)
                stats = NodeStats(
                    node_id=nid,
                    name=node["name"],
                    online=False,
                    memory_total=node.get("memory", 0),
                    memory_used=0,
                    disk_total=node.get("disk", 0),
                    disk_used=0,
                    cpu_count=node.get("cpu", 0),
                    allocated_resources={},
                    uptime=None,
                )
            except Exception as exc:
                log.error("Unexpected error for node %d: %s", nid, exc)
                stats = NodeStats(
                    node_id=nid,
                    name=node["name"],
                    online=False,
                    memory_total=0,
                    memory_used=0,
                    disk_total=0,
                    disk_used=0,
                    cpu_count=0,
                    allocated_resources={},
                    uptime=None,
                )

            embed = _build_node_embed(stats)
            view = build_node_view(
                self.bot.config.PANEL_URL,
                self.bot.config.STATUS_URL,
            )

            existing = await db.get_node_status_msg(nid, channel.id)
            if existing:
                try:
                    msg = await channel.fetch_message(existing["message_id"])
                    await msg.edit(embed=embed, view=view)
                    posted += 1
                    continue
                except discord.NotFound:
                    pass

            msg = await channel.send(embed=embed, view=view)
            await db.upsert_node_status_msg(nid, channel.id, msg.id)
            posted += 1

        await api.close()
        await interaction.followup.send(
            embed=success_embed(
                "Node Status Embeds Posted",
                f"Posted/updated {posted} node status embed(s) in {channel.mention}.",
            ),
            ephemeral=True,
        )

    # ── /node-uptime ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="node-uptime",
        description="Post a live-updating node uptime overview embed.",
    )
    @app_commands.describe(channel="The channel to post the uptime embed in.")
    @require_any_admin()
    async def node_uptime(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        api = self._api()
        db = self.bot.db

        try:
            nodes = await api.list_nodes()
        except PterodactylError as exc:
            await interaction.followup.send(
                embed=error_embed("API Error", f"Could not fetch nodes: {exc}"),
                ephemeral=True,
            )
            await api.close()
            return

        # For initial posting, build a basic embed; the monitor loop will enrich it
        online_map: dict[int, bool] = {}
        for node in nodes:
            try:
                stats = await api.get_node_stats(node["id"])
                online_map[node["id"]] = stats.online
            except Exception:
                online_map[node["id"]] = False

        # Build simple initial embed (monitor loop will replace with full uptime embed)
        lines = []
        for node in nodes:
            icon = "🟢" if online_map.get(node["id"], False) else "🔴"
            status = "Online" if online_map.get(node["id"], False) else "Offline"
            lines.append(f"{icon} **{node['name']}** — {status}")

        embed = discord.Embed(
            title="🕒 Node Uptime Overview",
            description="\n".join(lines) or "No nodes found.",
            colour=GOLD,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Auto-refreshes every ~45s")

        existing_rows = await db.get_uptime_msgs()
        existing = next((r for r in existing_rows if r["channel_id"] == channel.id), None)

        if existing:
            try:
                msg = await channel.fetch_message(existing["message_id"])
                await msg.edit(embed=embed)
                await interaction.followup.send(
                    embed=success_embed("Updated", f"Uptime embed refreshed in {channel.mention}."),
                    ephemeral=True,
                )
                await api.close()
                return
            except discord.NotFound:
                pass

        msg = await channel.send(embed=embed)
        await db.upsert_uptime_msg(channel.id, msg.id)
        await api.close()

        await interaction.followup.send(
            embed=success_embed(
                "Uptime Embed Posted",
                f"Uptime overview embed posted in {channel.mention}. It will auto-update every ~45s with full uptime statistics.",
            ),
            ephemeral=True,
        )

    # ── /node-maintenance ──────────────────────────────────────────────────────

    @app_commands.command(
        name="node-maintenance",
        description="Register a channel to receive automatic maintenance/recovery alerts.",
    )
    @app_commands.describe(channel="Channel to receive maintenance alerts.")
    @require_any_admin()
    async def node_maintenance(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.set_maintenance_channel(channel.id)

        await interaction.followup.send(
            embed=success_embed(
                "Maintenance Channel Set",
                (
                    f"Maintenance and recovery alerts will be sent to {channel.mention}.\n\n"
                    "When a node goes offline the bot will:\n"
                    "• Post a maintenance embed and mention main admins\n"
                    "• Edit the embed to show recovery once the node is back\n"
                    "• Log the downtime duration"
                ),
            ),
            ephemeral=True,
        )


# ── Standalone embed builder used by both nodes.py and node_monitor.py ────────

def _build_node_embed(stats: NodeStats) -> discord.Embed:
    colour = GREEN if stats.online else RED
    status_icon = "🟢 ONLINE" if stats.online else "🔴 OFFLINE"
    now = datetime.now(timezone.utc)

    embed = discord.Embed(
        title=f"📊 Node Status — {stats.name}",
        colour=colour,
        timestamp=now,
    )
    embed.set_footer(
        text=f"Node ID: {stats.node_id}  •  Last checked: {now.strftime('%H:%M:%S UTC')}"
    )
    embed.add_field(name="Status", value=status_icon, inline=True)

    if stats.online:
        ram_bar = progress_bar(stats.memory_used, stats.memory_total)
        disk_bar = progress_bar(stats.disk_used, stats.disk_total)
        embed.add_field(
            name="💾 RAM",
            value=(
                f"{fmt_bytes(stats.memory_used)} / {fmt_bytes(stats.memory_total)}\n"
                f"`{ram_bar}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="🗄 Disk",
            value=(
                f"{fmt_bytes(stats.disk_used)} / {fmt_bytes(stats.disk_total)}\n"
                f"`{disk_bar}`"
            ),
            inline=True,
        )
        embed.add_field(name="⚙️ CPU Cores", value=str(stats.cpu_count), inline=True)
    else:
        embed.add_field(
            name="⚠️ Wings Unreachable",
            value=(
                "The Wings daemon is not responding.\n"
                "The node may be offline or under maintenance."
            ),
            inline=False,
        )
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NodeCommands(bot))
