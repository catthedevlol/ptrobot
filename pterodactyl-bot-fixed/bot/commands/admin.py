"""
commands/admin.py — Admin management slash commands.

/admin-add @user    — Grant sub-admin permissions (main admins only)
/admin-remove @user — Revoke sub-admin permissions (main admins only)
/admin-list         — List all sub-admins
/set-log-channel    — Set the channel for bot log embeds
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import (
    require_main_admin,
    require_any_admin,
    success_embed,
    error_embed,
    info_embed,
    log_embed,
    BLUE,
    GREEN,
    RED,
)

log = logging.getLogger("bot.cmd.admin")


class AdminCommands(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /admin-add ────────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin-add",
        description="[Main Admin Only] Grant sub-admin permissions to a member.",
    )
    @app_commands.describe(member="The member to promote to sub-admin.")
    @require_main_admin()
    async def admin_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Prevent adding main admins (they're always admins anyway)
        if member.id in self.bot.config.MAIN_ADMIN_IDS:
            await interaction.followup.send(
                embed=info_embed("Already Main Admin", f"{member.mention} is a main admin."),
                ephemeral=True,
            )
            return

        if await self.bot.db.is_admin(member.id):
            await interaction.followup.send(
                embed=info_embed("Already Sub-Admin", f"{member.mention} is already a sub-admin."),
                ephemeral=True,
            )
            return

        await self.bot.db.add_admin(member.id, interaction.user.id)

        await interaction.followup.send(
            embed=success_embed(
                "Sub-Admin Added",
                f"{member.mention} has been granted sub-admin permissions.\n"
                "They can now use `/create-user` and `/create-server`.",
            ),
            ephemeral=True,
        )

        await self._push_log(
            interaction,
            "Admin Added",
            f"**{member.mention}** was granted sub-admin by {interaction.user.mention}.",
        )

    # ── /admin-remove ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin-remove",
        description="[Main Admin Only] Revoke sub-admin permissions from a member.",
    )
    @app_commands.describe(member="The member to demote.")
    @require_main_admin()
    async def admin_remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if member.id in self.bot.config.MAIN_ADMIN_IDS:
            await interaction.followup.send(
                embed=error_embed(
                    "Cannot Remove",
                    f"{member.mention} is a main admin and cannot be removed via this command.",
                ),
                ephemeral=True,
            )
            return

        removed = await self.bot.db.remove_admin(member.id, interaction.user.id)
        if not removed:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"{member.mention} is not a sub-admin."),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed(
                "Sub-Admin Removed",
                f"{member.mention}'s sub-admin permissions have been revoked.",
            ),
            ephemeral=True,
        )

        await self._push_log(
            interaction,
            "Admin Removed",
            f"**{member.mention}** had sub-admin revoked by {interaction.user.mention}.",
        )

    # ── /admin-list ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin-list",
        description="List all main admins and sub-admins.",
    )
    @require_any_admin()
    async def admin_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="👮 Admin List",
            colour=BLUE,
            timestamp=datetime.now(timezone.utc),
        )

        # Main admins
        main_lines = []
        for uid in self.bot.config.MAIN_ADMIN_IDS:
            try:
                member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
                main_lines.append(f"• {member.mention} (`{uid}`)")
            except Exception:
                main_lines.append(f"• `{uid}` (not in server)")

        embed.add_field(
            name="⭐ Main Admins",
            value="\n".join(main_lines) or "None",
            inline=False,
        )

        # Sub-admins
        sub_rows = await self.bot.db.list_admins()
        sub_lines = []
        for row in sub_rows:
            uid = row["discord_id"]
            try:
                member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
                sub_lines.append(f"• {member.mention} — added <t:{int(datetime.fromisoformat(row["added_at"].replace("Z", "+00:00")).timestamp())}:R>")
            except Exception:
                sub_lines.append(f"• `{uid}` (not in server)")

        embed.add_field(
            name="🔧 Sub-Admins",
            value="\n".join(sub_lines) or "None configured.",
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /set-log-channel ──────────────────────────────────────────────────────

    @app_commands.command(
        name="set-log-channel",
        description="[Main Admin Only] Set the channel for bot action logs.",
    )
    @app_commands.describe(channel="The channel to receive log embeds.")
    @require_main_admin()
    async def set_log_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        await self.bot.db.set_log_channel(channel.id)
        await interaction.response.send_message(
            embed=success_embed(
                "Log Channel Set",
                f"All bot action logs will now be sent to {channel.mention}.",
            ),
            ephemeral=True,
        )

    # ── Log helper ────────────────────────────────────────────────────────────

    async def _push_log(
        self,
        interaction: discord.Interaction,
        action: str,
        details: str,
    ) -> None:
        log_ch_id = await self.bot.db.get_log_channel()
        if not log_ch_id:
            return
        try:
            ch = self.bot.get_channel(log_ch_id) or await self.bot.fetch_channel(log_ch_id)
            await ch.send(embed=log_embed(action, interaction.user, details))
        except Exception as exc:
            log.error("Log push failed: %s", exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))
