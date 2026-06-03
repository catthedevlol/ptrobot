"""
commands/users.py — User management slash commands.

Fixes applied:
- Username sanitization handles leading ., _, - characters.
- Retries with a fresh username on 422 "username already exists" errors.
- All panel errors produce clean Discord embeds — no raw tracebacks.
- DM embed improved with panel link button.
- Log embed sent to log channel on success and on failure.
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
    info_embed,
    log_embed,
    GREEN,
    RED,
    BLUE,
)
from utils.ptero_api import PterodactylAPI, PterodactylError

log = logging.getLogger("bot.cmd.users")

# Maximum retries if a generated username collides on the panel
_MAX_USERNAME_RETRIES = 5


class UserCommands(commands.Cog, name="Users"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _api(self) -> PterodactylAPI:
        return PterodactylAPI(
            self.bot.config.PANEL_URL,
            self.bot.config.PANEL_API_KEY,
        )

    # ── /create-user ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="create-user",
        description="Create a Pterodactyl panel account for a Discord member.",
    )
    @app_commands.describe(member="The Discord member to create an account for.")
    @require_any_admin()
    async def create_user(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        db = self.bot.db
        api = self._api()

        # ── Already has an account? ──────────────────────────────────────────
        existing = await db.get_ptero_user(member.id)
        if existing:
            await interaction.followup.send(
                embed=info_embed(
                    "Account Exists",
                    (
                        f"{member.mention} already has a Pterodactyl account.\n\n"
                        f"**Username:** `{existing['username']}`\n"
                        f"**Panel ID:** `{existing['ptero_id']}`\n"
                        f"**Created:** <t:{int(datetime.fromisoformat(existing['created_at'].replace('Z','+'+'00:00')).timestamp())}:R>"
                    ),
                ),
                ephemeral=True,
            )
            await api.close()
            return

        # ── Generate credentials with collision retry ─────────────────────────
        password = PterodactylAPI.generate_password()
        ptero_user: dict | None = None
        username: str = ""
        last_error: PterodactylError | None = None

        for attempt in range(1, _MAX_USERNAME_RETRIES + 1):
            username = PterodactylAPI.generate_username(member.display_name)
            email = f"{username}@panel.local"
            log.debug(
                "create_user attempt %d/%d username=%s for discord=%s",
                attempt, _MAX_USERNAME_RETRIES, username, member,
            )
            try:
                ptero_user = await api.create_user(
                    username=username,
                    email=email,
                    first_name=member.display_name[:32],
                    last_name="User",
                    password=password,
                )
                break  # success
            except PterodactylError as exc:
                last_error = exc
                # 422 = validation error — usually a username/email conflict
                if exc.status in (422, 409):
                    log.warning(
                        "Username %s rejected (attempt %d): %s — retrying",
                        username, attempt, exc,
                    )
                    continue
                # Any other error is fatal
                log.error("Panel user creation failed for %s: %s", member, exc)
                await interaction.followup.send(
                    embed=_api_error_embed("User Creation Failed", exc),
                    ephemeral=True,
                )
                await self._push_log(
                    interaction,
                    action="Failed User Creation",
                    details=(
                        f"**Discord:** {member.mention} (`{member.id}`)\n"
                        f"**Error:** `{exc}`"
                    ),
                )
                await api.close()
                return

        await api.close()

        if ptero_user is None:
            # All retries exhausted
            log.error(
                "All %d username attempts failed for %s. Last error: %s",
                _MAX_USERNAME_RETRIES, member, last_error,
            )
            await interaction.followup.send(
                embed=error_embed(
                    "User Creation Failed",
                    (
                        f"Could not generate a unique username after "
                        f"{_MAX_USERNAME_RETRIES} attempts.\n\n"
                        f"Last panel error: `{last_error}`"
                    ),
                ),
                ephemeral=True,
            )
            await self._push_log(
                interaction,
                action="Failed User Creation",
                details=(
                    f"**Discord:** {member.mention} (`{member.id}`)\n"
                    f"**Reason:** Username collision after {_MAX_USERNAME_RETRIES} retries\n"
                    f"**Last error:** `{last_error}`"
                ),
            )
            return

        ptero_id = ptero_user["id"]

        # ── Save to database ──────────────────────────────────────────────────
        await db.create_ptero_user(
            discord_id=member.id,
            ptero_id=ptero_id,
            username=username,
            email=email,
            password=password,
            created_by=interaction.user.id,
        )

        # ── DM user credentials ───────────────────────────────────────────────
        dm_embed = discord.Embed(
            title="🎉 Your Pterodactyl Account is Ready!",
            description=(
                "Your panel account has been created. Keep these credentials safe!\n\n"
                "**Do not share your password with anyone.**"
            ),
            colour=GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.add_field(name="🌐 Panel URL", value=self.bot.config.PANEL_URL, inline=False)
        dm_embed.add_field(name="👤 Username", value=f"`{username}`", inline=True)
        dm_embed.add_field(name="📧 Email", value=f"`{email}`", inline=True)
        dm_embed.add_field(name="🔑 Password", value=f"||`{password}`||", inline=False)
        dm_embed.set_footer(text="If you have issues logging in, contact an admin.")

        panel_view = discord.ui.View(timeout=None)
        panel_view.add_item(
            discord.ui.Button(
                label="🖥 Open Panel",
                url=self.bot.config.PANEL_URL,
                style=discord.ButtonStyle.link,
            )
        )

        dm_sent = True
        try:
            await member.send(embed=dm_embed, view=panel_view)
        except discord.Forbidden:
            dm_sent = False
            log.warning("Could not DM credentials to %s — DMs disabled", member)

        # ── Confirm to admin ──────────────────────────────────────────────────
        confirm = discord.Embed(
            title="✅ User Created",
            colour=GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        confirm.add_field(name="Discord", value=member.mention, inline=True)
        confirm.add_field(name="Panel ID", value=str(ptero_id), inline=True)
        confirm.add_field(name="Username", value=f"`{username}`", inline=True)
        confirm.add_field(
            name="DM Sent",
            value="✅ Yes" if dm_sent else "⚠️ No (DMs disabled)",
            inline=True,
        )
        await interaction.followup.send(embed=confirm, ephemeral=True)

        # ── Push to log channel ───────────────────────────────────────────────
        await self._push_log(
            interaction,
            action="User Created",
            details=(
                f"**Discord:** {member.mention} (`{member.id}`)\n"
                f"**Panel ID:** `{ptero_id}`\n"
                f"**Username:** `{username}`\n"
                f"**Created by:** {interaction.user.mention}\n"
                f"**DM sent:** {'Yes' if dm_sent else 'No — DMs disabled'}"
            ),
        )

    # ── Log helper ─────────────────────────────────────────────────────────────

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
            ch = (
                self.bot.get_channel(log_ch_id)
                or await self.bot.fetch_channel(log_ch_id)
            )
            embed = log_embed(action, interaction.user, details)
            await ch.send(embed=embed)
        except Exception as exc:
            log.error("Failed to push log: %s", exc)


# ── Error embed helper ────────────────────────────────────────────────────────

def _api_error_embed(title: str, exc: PterodactylError) -> discord.Embed:
    """Build a friendly embed for a known PterodactylError."""
    status_hints = {
        0:   "The panel is unreachable. Check your network or panel URL.",
        403: "API key does not have permission to perform this action.",
        404: "The requested resource was not found on the panel.",
        409: "A conflict occurred — the resource may already exist.",
        422: "The panel rejected the request due to invalid data.",
        500: "The panel returned an internal server error.",
        503: "The panel is temporarily unavailable.",
    }
    hint = status_hints.get(exc.status, "")
    description = f"**Error:** `{exc}`"
    if hint:
        description += f"\n\n{hint}"
    embed = discord.Embed(title=f"❌ {title}", description=description, colour=RED)
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserCommands(bot))
