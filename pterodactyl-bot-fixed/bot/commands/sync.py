"""
commands/sync.py — /sync command.

Performs a full system refresh:
  • Reloads all cogs
  • Re-syncs slash commands to the guild
  • Restarts background tasks
  • Verifies DB integrity (missing users, orphan servers)
  • Reloads egg and node cache
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import require_main_admin, success_embed, error_embed, BLUE
from utils.ptero_api import PterodactylAPI, PterodactylError

log = logging.getLogger("bot.cmd.sync")

# Cogs that are reloaded during /sync (excluding sync itself to avoid recursion)
RELOAD_COGS = [
    "commands.admin",
    "commands.nodes",
    "commands.users",
    "commands.servers",
    "tasks.node_monitor",
]


class SyncCommands(commands.Cog, name="Sync"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="sync",
        description="[Main Admin Only] Reload commands, tasks, and verify panel integrity.",
    )
    @require_main_admin()
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        results: list[str] = []

        # ── 1. Reload cogs ────────────────────────────────────────────────────
        cog_ok, cog_fail = 0, 0
        for cog in RELOAD_COGS:
            try:
                await self.bot.reload_extension(cog)
                cog_ok += 1
            except Exception as exc:
                log.error("Failed to reload %s: %s", cog, exc)
                cog_fail += 1
        results.append(f"🔄 Cogs: {cog_ok} reloaded, {cog_fail} failed")

        # ── 2. Re-sync slash commands to guild ────────────────────────────────
        try:
            guild = discord.Object(id=self.bot.config.GUILD_ID)
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            results.append(f"✅ Slash commands: {len(synced)} synced")
        except Exception as exc:
            log.error("Slash command sync failed: %s", exc)
            results.append(f"❌ Slash command sync failed: {exc}")

        # ── 3. Restart background tasks ───────────────────────────────────────
        monitor_cog = self.bot.cogs.get("NodeMonitorTask")
        if monitor_cog and hasattr(monitor_cog, "restart"):
            monitor_cog.restart()
            results.append("🔁 Background tasks restarted")
        else:
            results.append("⚠️ NodeMonitorTask cog not found")

        # ── 4. Reload node + egg cache ────────────────────────────────────────
        api = PterodactylAPI(self.bot.config.PANEL_URL, self.bot.config.PANEL_API_KEY)
        try:
            nodes = await api.list_nodes()
            results.append(f"🖥 Nodes loaded: {len(nodes)}")

            total_eggs = 0
            nests = await api.list_nests()
            for nest in nests:
                eggs = await api.list_eggs(nest["id"])
                total_eggs += len(eggs)
            results.append(f"🥚 Eggs loaded: {total_eggs}")
        except PterodactylError as exc:
            results.append(f"❌ Panel API error: {exc}")
        finally:
            await api.close()

        # ── 5. Verify missing panel users ─────────────────────────────────────
        try:
            await self._verify_users(results)
        except Exception as exc:
            results.append(f"⚠️ User verification error: {exc}")

        # ── 6. Verify orphan allocations / servers ────────────────────────────
        try:
            await self._verify_servers(results)
        except Exception as exc:
            results.append(f"⚠️ Server verification error: {exc}")

        # ── Build response embed ──────────────────────────────────────────────
        embed = discord.Embed(
            title="🔄 Sync Complete",
            description="\n".join(results),
            colour=BLUE,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Triggered by {interaction.user}")

        await interaction.followup.send(embed=embed, ephemeral=True)

        # ── Push to log channel ───────────────────────────────────────────────
        log_ch_id = await self.bot.db.get_log_channel()
        if log_ch_id:
            try:
                ch = self.bot.get_channel(log_ch_id) or await self.bot.fetch_channel(log_ch_id)
                log_embed = discord.Embed(
                    title="🔄 /sync executed",
                    description=(
                        f"**Triggered by:** {interaction.user.mention}\n\n"
                        + "\n".join(results)
                    ),
                    colour=BLUE,
                    timestamp=datetime.now(timezone.utc),
                )
                await ch.send(embed=log_embed)
            except Exception as exc:
                log.error("Log push failed: %s", exc)

    # ── Integrity checks ──────────────────────────────────────────────────────

    async def _verify_users(self, results: list[str]) -> None:
        """Check that every DB user still exists on the panel."""
        db = self.bot.db
        api = PterodactylAPI(self.bot.config.PANEL_URL, self.bot.config.PANEL_API_KEY)

        try:
            panel_users = await api.list_users()
            panel_ids = {u["id"] for u in panel_users}

            db_users = await db.fetchall("SELECT discord_id, ptero_id, username FROM pterodactyl_users")
            missing = [u for u in db_users if u["ptero_id"] not in panel_ids]

            if missing:
                results.append(
                    f"⚠️ Users missing from panel: {len(missing)} "
                    f"(IDs: {', '.join(str(u['ptero_id']) for u in missing[:5])})"
                )
            else:
                results.append(f"✅ Users verified: {len(db_users)} in DB, all present on panel")
        finally:
            await api.close()

    async def _verify_servers(self, results: list[str]) -> None:
        """Check that every DB server still exists on the panel."""
        db = self.bot.db
        api = PterodactylAPI(self.bot.config.PANEL_URL, self.bot.config.PANEL_API_KEY)

        try:
            panel_servers = await api.list_servers()
            panel_ids = {s["id"] for s in panel_servers}

            db_servers = await db.fetchall(
                "SELECT ptero_server_id, name FROM pterodactyl_servers"
            )
            orphans = [s for s in db_servers if s["ptero_server_id"] not in panel_ids]

            if orphans:
                results.append(
                    f"⚠️ Orphan server records in DB: {len(orphans)} "
                    f"(IDs: {', '.join(str(s['ptero_server_id']) for s in orphans[:5])})"
                )
            else:
                results.append(f"✅ Servers verified: {len(db_servers)} in DB, all present on panel")
        finally:
            await api.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SyncCommands(bot))
