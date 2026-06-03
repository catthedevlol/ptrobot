"""
Pterodactyl Panel Discord Bot — Main Entrypoint
Loads all cogs, initialises the database, and starts the bot.
"""

import asyncio
import logging
import os
import sys

# Load .env before anything else so config.py can read env-vars
from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands

from config import BotConfig
from database import Database

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bot.main")

# ── Cogs to load (order matters: core first) ─────────────────────────────────
COGS: list[str] = [
    "commands.admin",
    "commands.nodes",
    "commands.users",
    "commands.servers",
    "commands.sync",
    "tasks.node_monitor",
]


class PterodactylBot(commands.Bot):
    """Custom Bot subclass — holds shared resources."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True       # needed for DMs and member lookups
        intents.message_content = True

        super().__init__(
            command_prefix="!",      # legacy prefix (unused — slash only)
            intents=intents,
            help_command=None,
        )

        self.config = BotConfig()
        self.db: Database | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called once before the bot connects — ideal for async setup."""
        # Initialise database
        self.db = Database(self.config.DATABASE_URL)
        await self.db.init()

        # Add persistent view so buttons survive restarts
        from views.persistent import PersistentNodeView
        self.add_view(PersistentNodeView())

        # Load cogs
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as exc:
                log.error("Failed to load cog %s: %s", cog, exc, exc_info=True)

        # Sync slash commands to the target guild only (instant — no 1-hour delay)
        guild = discord.Object(id=self.config.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d slash command(s) to guild %d", len(synced), self.config.GUILD_ID)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Pterodactyl Nodes",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Leave any guild that is not the authorised guild."""
        if guild.id != self.config.GUILD_ID:
            log.warning("Joined unauthorised guild %s (%d) — leaving.", guild.name, guild.id)
            await guild.leave()

    async def on_application_command_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        """Global slash-command error handler."""
        log.error("Slash command error: %s", error, exc_info=True)
        msg = "An unexpected error occurred. Please try again later."
        if isinstance(error, commands.MissingPermissions):
            msg = "You do not have permission to use this command."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
        except discord.HTTPException:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with PterodactylBot() as bot:
        await bot.start(BotConfig().BOT_TOKEN)


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    asyncio.run(main())
