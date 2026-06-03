"""
utils/helpers.py — Shared utility functions.

• Permission guard decorators for slash commands
• Embed factory functions
• Formatting helpers
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

log = logging.getLogger("bot.helpers")

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = 0x2ECC71
RED    = 0xE74C3C
ORANGE = 0xE67E22
BLUE   = 0x3498DB
PURPLE = 0x9B59B6
GREY   = 0x95A5A6
GOLD   = 0xF1C40F


# ─────────────────────────────────────────────────────────────────────────────
# Permission checks
# ─────────────────────────────────────────────────────────────────────────────

def is_main_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in interaction.client.config.MAIN_ADMIN_IDS


async def is_authorised(interaction: discord.Interaction) -> bool:
    if is_main_admin(interaction):
        return True
    return await interaction.client.db.is_admin(interaction.user.id)


def require_main_admin() -> app_commands.check:
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_main_admin(interaction):
            embed = error_embed(
                "Permission Denied",
                "Only main administrators can use this command.",
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)


def require_any_admin() -> app_commands.check:
    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_authorised(interaction):
            return True
        embed = error_embed(
            "Permission Denied",
            "You need admin permissions to use this command.",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return app_commands.check(predicate)


# ─────────────────────────────────────────────────────────────────────────────
# Embed builders
# ─────────────────────────────────────────────────────────────────────────────

def base_embed(
    title: str,
    description: str = "",
    colour: int = BLUE,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour,
        timestamp=datetime.now(timezone.utc),
    )
    return embed


def success_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, GREEN)


def error_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, RED)


def warning_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, ORANGE)


def info_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, BLUE)


def log_embed(action: str, actor: discord.Member | None, details: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {action}",
        description=details,
        colour=PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    if actor:
        embed.set_author(name=str(actor), icon_url=actor.display_avatar.url)
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

def fmt_bytes(mb: int) -> str:
    """Format megabytes into a human-readable string."""
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb} MB"


def fmt_duration(seconds: int) -> str:
    """Format seconds into a compact human-readable duration string."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m, _ = divmod(rem, 60)
        return f"{h}h {m}m"
    d, rem = divmod(seconds, 86400)
    h, rem2 = divmod(rem, 3600)
    m, _ = divmod(rem2, 60)
    return f"{d}d {h}h {m}m"


def fmt_ago(dt: datetime | None) -> str:
    """Format a datetime as 'X ago' relative to now."""
    if dt is None:
        return "Unknown"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = int((now - dt).total_seconds())
    return fmt_duration(diff) + " ago"


def progress_bar(used: int, total: int, length: int = 10) -> str:
    if total == 0:
        return "░" * length
    ratio = min(used / total, 1.0)
    filled = round(ratio * length)
    bar = "█" * filled + "░" * (length - filled)
    pct = ratio * 100
    return f"{bar} {pct:.0f}%"


def uptime_pct(duration_s: int, window_s: int = 86400) -> str:
    if window_s == 0:
        return "N/A"
    up = max(window_s - duration_s, 0)
    return f"{(up / window_s) * 100:.2f}%"
