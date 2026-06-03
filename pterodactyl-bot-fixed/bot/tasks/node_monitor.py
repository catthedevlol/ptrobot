"""
tasks/node_monitor.py — Background task cog with real offline detection.

PHASE 2 FIXES:
- Real Wings probing (TCP + HTTP dual-check)
- Configurable offline/recovery thresholds
- State persistence survives bot restarts
- Maintenance alerts with proper recovery handling
- No fake online status
- Accurate color-coded embeds (RED/GREEN based on real state)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from utils.ptero_api import PterodactylAPI, PterodactylError, NodeStats
from utils.node_checker import NodeChecker
from utils.helpers import (
    fmt_bytes,
    fmt_duration,
    progress_bar,
    GREEN,
    RED,
    ORANGE,
    GOLD,
    BLUE,
)
from views.persistent import build_node_view

log = logging.getLogger("bot.task.node_monitor")

# In-memory state cache: node_id → bool (is online)
# Loaded from DB on startup so we don't mis-fire transitions on restart.
_node_was_online: dict[int, bool] = {}
_state_loaded: bool = False

# Offline detection thresholds (configurable)
# A node is marked OFFLINE only after N consecutive failed checks
OFFLINE_THRESHOLD = 3  # 3 consecutive failures = offline (45s with 15s interval)
RECOVERY_THRESHOLD = 2  # 2 consecutive successes = online (30s recovery time)

# Track consecutive failures per node
_consecutive_failures: dict[int, int] = {}
_consecutive_successes: dict[int, int] = {}


# ────────────────────────────────────────────────────────────────
# Embed builders
# ────────────────────────────────────────────────────────────────

def _build_status_embed(stats: NodeStats, panel_url: str) -> discord.Embed:
    """Build the per-node live-status embed."""
    colour = GREEN if stats.online else RED
    status_icon = "🟢 ONLINE" if stats.online else "🔴 OFFLINE"
    now = datetime.now(timezone.utc)

    embed = discord.Embed(
        title=f"📊 Node: {stats.name}",
        colour=colour,
        timestamp=now,
    )
    embed.set_footer(
        text=f"Node ID: {stats.node_id}  •  Last checked: {now.strftime('%H:%M:%S UTC')}"
    )
    embed.add_field(name="Status", value=status_icon, inline=True)

    if stats.online:
        # Only show resource metrics if actually online
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
                "The Wings daemon is not responding to health checks.\n"
                "The node is offline or experiencing network issues."
            ),
            inline=False,
        )

    return embed


def _build_uptime_embed(
    nodes: list[dict],
    online_map: dict[int, bool],
    uptime_rows: dict[int, dict],  # node_id → uptime row dict
    monthly_pcts: dict[int, float],
) -> discord.Embed:
    """
    Build the advanced uptime overview embed.

    🟢 Germany-1
    ↳ Online For: 4d 12h 22m
    ↳ Monthly Uptime: 99.98%
    ↳ Total Downtime: 12m

    🔴 Singapore-1
    ↳ Offline Since: 18m ago
    ↳ Last Online Session: 2d 8h
    ↳ Last Downtime: 4m
    ↳ Monthly Uptime: 97.21%
    """
    now = datetime.now(timezone.utc)
    embed = discord.Embed(
        title="🕒 Node Uptime Overview",
        colour=GOLD,
        timestamp=now,
    )
    embed.set_footer(text=f"Last updated: {now.strftime('%H:%M:%S UTC')}")

    lines: list[str] = []
    for node in nodes:
        nid = node["id"]
        online = online_map.get(nid, False)
        row = uptime_rows.get(nid, {})
        monthly_pct = monthly_pcts.get(nid, 100.0)
        total_down_s = row.get("total_downtime_s", 0) or 0

        if online:
            lines.append(f"🟢 **{node['name']}**")
            # How long has it been online?
            online_since = row.get("online_since")
            if online_since:
                try:
                    dt = datetime.fromisoformat(online_since.replace("Z", "+00:00"))
                    online_for = fmt_duration(int((now - dt).total_seconds()))
                    lines.append(f"  ↳ Online For: {online_for}")
                except Exception:
                    pass
            lines.append(f"  ↳ Monthly Uptime: {monthly_pct:.2f}%")
            if total_down_s > 0:
                lines.append(f"  ↳ Total Downtime: {fmt_duration(total_down_s)}")
        else:
            lines.append(f"🔴 **{node['name']}**")
            # How long has it been offline?
            offline_since = row.get("offline_since")
            if offline_since:
                try:
                    dt = datetime.fromisoformat(offline_since.replace("Z", "+00:00"))
                    offline_for = fmt_duration(int((now - dt).total_seconds()))
                    lines.append(f"  ↳ Offline Since: {offline_for} ago")
                except Exception:
                    lines.append("  ↳ Offline Since: Unknown")
            else:
                lines.append("  ↳ Offline Since: Unknown")
            last_session_s = row.get("last_online_session_s", 0) or 0
            if last_session_s > 0:
                lines.append(f"  ↳ Last Online Session: {fmt_duration(last_session_s)}")
            last_down_s = row.get("last_downtime_s", 0) or 0
            if last_down_s > 0:
                lines.append(f"  ↳ Last Downtime: {fmt_duration(last_down_s)}")
            lines.append(f"  ↳ Monthly Uptime: {monthly_pct:.2f}%")

    embed.description = "\n".join(lines) if lines else "No nodes found."
    return embed


def _build_maintenance_embed(node_name: str, admin_mentions: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔧 Maintenance Alert — {node_name}",
        description=(
            f"⚠️ **Node `{node_name}` has gone offline.**\n\n"
            "Our team has been notified and is investigating.\n\n"
            f"{admin_mentions}"
        ),
        colour=RED,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="This message will be updated when the node recovers.")
    return embed


def _build_recovery_embed(node_name: str, downtime_s: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"✅ Node Recovered — {node_name}",
        description=(
            f"**Node `{node_name}` is back online!**\n\n"
            f"🕐 Total downtime: **{fmt_duration(downtime_s)}**"
        ),
        colour=GREEN,
        timestamp=datetime.now(timezone.utc),
    )
    return embed


# ────────────────────────────────────────────────────────────────
# Cog
# ────────────────────────────────────────────────────────────────

class NodeMonitorTask(commands.Cog):
    """Background cog that keeps node embeds up-to-date with REAL offline detection."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._api: PterodactylAPI | None = None
        self._checker: NodeChecker | None = None

    # ── Cog lifecycle ────────────────────────────────────────────────────────

    async def cog_load(self) -> None:
        self._api = PterodactylAPI(
            self.bot.config.PANEL_URL,
            self.bot.config.PANEL_API_KEY,
        )
        self._checker = NodeChecker()
        self.monitor_loop.change_interval(
            seconds=self.bot.config.NODE_POLL_INTERVAL
        )
        self.monitor_loop.start()
        log.info(
            "NodeMonitorTask started (interval=%ds, offline_threshold=%d, recovery_threshold=%d)",
            self.bot.config.NODE_POLL_INTERVAL,
            OFFLINE_THRESHOLD,
            RECOVERY_THRESHOLD,
        )

    async def cog_unload(self) -> None:
        self.monitor_loop.cancel()
        if self._api:
            await self._api.close()

    # ── Main loop ─────────────────────────────────────────────────────────

    @tasks.loop(seconds=45)
    async def monitor_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            await self._run_cycle()
        except Exception as exc:
            log.error("monitor_loop unhandled error: %s", exc, exc_info=True)

    async def _run_cycle(self) -> None:
        """Single monitoring cycle: probe nodes, handle transitions, update embeds."""
        global _state_loaded

        db = self.bot.db

        # On first cycle, load persisted state from DB so we don't replay transitions
        if not _state_loaded:
            await self._load_state_from_db()
            _state_loaded = True

        # Fetch node list from panel
        try:
            nodes = await self._api.list_nodes()
        except PterodactylError as exc:
            log.warning("Could not list nodes: %s", exc)
            return

        # Real Wings probing for each node — use threshold-based logic
        online_map: dict[int, bool] = {}
        stats_map: dict[int, NodeStats | None] = {}

        for node in nodes:
            nid = node["id"]
            
            # Use NodeChecker for real Wings probing
            check_result = await self._checker.check_node(node)
            is_online = check_result.online
            
            # Update failure/success counters
            if is_online:
                _consecutive_failures[nid] = 0
                _consecutive_successes[nid] = _consecutive_successes.get(nid, 0) + 1
            else:
                _consecutive_successes[nid] = 0
                _consecutive_failures[nid] = _consecutive_failures.get(nid, 0) + 1

            # Apply thresholds: only mark online/offline after N consecutive checks
            threshold_online = _consecutive_successes.get(nid, 0) >= RECOVERY_THRESHOLD
            threshold_offline = _consecutive_failures.get(nid, 0) >= OFFLINE_THRESHOLD

            # Determine actual state based on thresholds
            if threshold_offline and _consecutive_failures[nid] >= OFFLINE_THRESHOLD:
                online_map[nid] = False
            elif threshold_online and _consecutive_successes[nid] >= RECOVERY_THRESHOLD:
                online_map[nid] = True
            else:
                # Still in threshold accumulation phase — keep previous state
                online_map[nid] = _node_was_online.get(nid, True)

            # Build stats for embed display
            if online_map[nid]:
                # Node online — get real resource metrics
                try:
                    stats = await self._api.get_node_stats(nid)
                    stats_map[nid] = stats
                except PterodactylError as exc:
                    log.warning("Could not get stats for node %d: %s", nid, exc)
                    # Build offline placeholder
                    stats_map[nid] = NodeStats(
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
            else:
                # Node offline — build placeholder
                stats_map[nid] = NodeStats(
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

            log.debug(
                "[Monitor] %s: failures=%d, successes=%d, online=%s",
                node["name"],
                _consecutive_failures.get(nid, 0),
                _consecutive_successes.get(nid, 0),
                online_map[nid],
            )

        # Handle state transitions (offline → online, online → offline)
        await self._handle_transitions(nodes, online_map)

        # Refresh embeds with the latest data
        await self._refresh_status_embeds(nodes, stats_map, online_map)

        # Build advanced uptime data for the overview embed
        uptime_rows: dict[int, dict] = {}
        monthly_pcts: dict[int, float] = {}
        for node in nodes:
            nid = node["id"]
            row = await db.get_node_uptime(nid)
            if row:
                uptime_rows[nid] = dict(row)
            monthly_pcts[nid] = await db.get_monthly_uptime_pct(nid)

        await self._refresh_uptime_embeds(nodes, online_map, uptime_rows, monthly_pcts)

    # ── State loader ────────────────────────────────────────────────────────

    async def _load_state_from_db(self) -> None:
        """Populate _node_was_online from persisted uptime rows."""
        rows = await self.bot.db.get_all_node_uptimes()
        for row in rows:
            nid = row["node_id"]
            # Node is considered "was online" if offline_since is NULL
            _node_was_online[nid] = row["offline_since"] is None
        log.info(
            "Loaded node states from DB: %s",
            {k: ("online" if v else "offline") for k, v in _node_was_online.items()},
        )

    # ── Status embeds ────────────────────────────────────────────────────────

    async def _refresh_status_embeds(
        self,
        nodes: list[dict],
        stats_map: dict[int, NodeStats | None],
        online_map: dict[int, bool],
    ) -> None:
        db = self.bot.db
        rows = await db.get_node_status_msgs()
        node_lookup = {n["id"]: n for n in nodes}

        for row in rows:
            nid = row["node_id"]
            channel_id = row["channel_id"]
            message_id = row["message_id"]

            node = node_lookup.get(nid)
            if not node:
                continue

            stats = stats_map.get(nid)
            if stats is None:
                # Build an offline placeholder
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

            embed = _build_status_embed(stats, self.bot.config.PANEL_URL)
            view = build_node_view(
                self.bot.config.PANEL_URL,
                self.bot.config.STATUS_URL,
            )

            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed, view=view)
            except discord.NotFound:
                log.warning(
                    "Status embed not found (ch=%d msg=%d) — removing from DB",
                    channel_id, message_id,
                )
                await db.execute(
                    "DELETE FROM node_status_messages WHERE node_id=? AND channel_id=?",
                    (nid, channel_id),
                )
            except discord.Forbidden:
                log.warning("No permission to edit status embed ch=%d", channel_id)
            except Exception as exc:
                log.error("Failed to update status embed node=%d: %s", nid, exc)

    # ── Uptime embeds ────────────────────────────────────────────────────────

    async def _refresh_uptime_embeds(
        self,
        nodes: list[dict],
        online_map: dict[int, bool],
        uptime_rows: dict[int, dict],
        monthly_pcts: dict[int, float],
    ) -> None:
        db = self.bot.db
        rows = await db.get_uptime_msgs()

        embed = _build_uptime_embed(nodes, online_map, uptime_rows, monthly_pcts)

        for row in rows:
            channel_id = row["channel_id"]
            message_id = row["message_id"]
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
            except discord.NotFound:
                log.warning("Uptime embed not found (ch=%d msg=%d) — removing", channel_id, message_id)
                await db.execute(
                    "DELETE FROM node_uptime_messages WHERE channel_id=?",
                    (channel_id,),
                )
            except Exception as exc:
                log.error("Failed to update uptime embed: %s", exc)

    # ── Transition detection ───────────────────────────────────────────────────

    async def _handle_transitions(
        self,
        nodes: list[dict],
        online_map: dict[int, bool],
    ) -> None:
        db = self.bot.db
        maintenance_channels = await db.get_maintenance_channels()

        for node in nodes:
            nid = node["id"]
            now_online = online_map.get(nid, False)
            was_online = _node_was_online.get(nid, True)

            if now_online:
                # Always update last_seen_online
                if was_online:
                    await db.touch_node_online(nid, node["name"])
                else:
                    # Came back online (recovery)
                    log.info("Node %s (%d) came ONLINE", node["name"], nid)
                    await db.mark_node_online(nid, node["name"])
                    duration = await db.close_downtime(nid) or 0
                    if maintenance_channels:
                        await self._send_recovery_alert(node, duration, maintenance_channels)
                    await self._send_log(
                        "node_online",
                        details=(
                            f"Node **{node['name']}** (ID: {nid}) came back online. "
                            f"Downtime: {fmt_duration(duration)}"
                        ),
                    )
            else:
                if was_online:
                    # Just went OFFLINE
                    log.warning("Node %s (%d) went OFFLINE", node["name"], nid)
                    await db.mark_node_offline(nid, node["name"])
                    await db.open_downtime(nid, node["name"])
                    if maintenance_channels:
                        await self._send_maintenance_alert(node, maintenance_channels)
                    await self._send_log(
                        "node_offline",
                        details=f"Node **{node['name']}** (ID: {nid}) went offline.",
                    )
                # else: still offline — no action needed

            _node_was_online[nid] = now_online

    # ── Alert senders ────────────────────────────────────────────────────────

    async def _send_maintenance_alert(
        self, node: dict, maintenance_channels: list
    ) -> None:
        db = self.bot.db
        admin_ids = self.bot.config.MAIN_ADMIN_IDS
        mentions = " ".join(f"<@{aid}>" for aid in admin_ids)
        embed = _build_maintenance_embed(node["name"], mentions)

        for row in maintenance_channels:
            channel_id = row["channel_id"]
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg = await channel.send(content=mentions if mentions else None, embed=embed)
                await db.upsert_maintenance_msg(node["id"], channel_id, msg.id)
            except Exception as exc:
                log.error("Failed to send maintenance alert ch=%d: %s", channel_id, exc)

    async def _send_recovery_alert(
        self, node: dict, duration_s: int, maintenance_channels: list
    ) -> None:
        db = self.bot.db
        embed = _build_recovery_embed(node["name"], duration_s)

        for row in maintenance_channels:
            channel_id = row["channel_id"]
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                existing = await db.get_maintenance_msg(node["id"])
                if existing and existing["channel_id"] == channel_id:
                    try:
                        old_msg = await channel.fetch_message(existing["message_id"])
                        await old_msg.edit(embed=embed, view=None)
                        await db.delete_maintenance_msg(node["id"])
                        continue
                    except discord.NotFound:
                        pass

                await channel.send(embed=embed)
                await db.delete_maintenance_msg(node["id"])
            except Exception as exc:
                log.error("Failed to send recovery alert ch=%d: %s", channel_id, exc)

    # ── Log helper ─────────────────────────────────────────────────────────

    async def _send_log(self, action: str, details: str) -> None:
        log_channel_id = await self.bot.db.get_log_channel()
        if not log_channel_id:
            return
        try:
            channel = (
                self.bot.get_channel(log_channel_id)
                or await self.bot.fetch_channel(log_channel_id)
            )
            is_offline = "offline" in action
            icon = "🔴" if is_offline else "🟢"
            embed = discord.Embed(
                title=f"{icon} {action.replace('_', ' ').title()}",
                description=details,
                colour=0xE74C3C if is_offline else 0x2ECC71,
                timestamp=datetime.now(timezone.utc),
            )
            await channel.send(embed=embed)
        except Exception as exc:
            log.error("Failed to send log embed: %s", exc)

    # ── Public API ─────────────────────────────────────────────────────────

    def restart(self) -> None:
        global _state_loaded
        _state_loaded = False  # force re-load from DB on next cycle
        if self.monitor_loop.is_running():
            self.monitor_loop.restart()
        else:
            self.monitor_loop.start()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NodeMonitorTask(bot))
