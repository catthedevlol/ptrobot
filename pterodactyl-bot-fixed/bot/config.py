"""
config.py — Centralised configuration via environment variables.
"""

import os
from dataclasses import dataclass, field


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set.")
    return value


@dataclass
class BotConfig:
    # ── Discord ──────────────────────────────────────────────────────────────
    BOT_TOKEN: str = field(default_factory=lambda: _require("BOT_TOKEN"))
    GUILD_ID: int = field(default_factory=lambda: int(_require("GUILD_ID")))

    MAIN_ADMIN_IDS: list[int] = field(
        default_factory=lambda: [
            int(x.strip())
            for x in os.getenv(
                "MAIN_ADMIN_IDS",
                "1133055533318946837,1262068023481733283",
            ).split(",")
            if x.strip()
        ]
    )

    # ── Pterodactyl ──────────────────────────────────────────────────────────
    PANEL_URL: str = field(default_factory=lambda: _require("PANEL_URL").rstrip("/"))
    PANEL_API_KEY: str = field(default_factory=lambda: _require("PANEL_API_KEY"))

    # Optional: public status page URL shown in node embeds
    STATUS_URL: str = field(default_factory=lambda: os.getenv("STATUS_URL", ""))

    # Optional: support URL shown in server DM buttons
    SUPPORT_URL: str = field(default_factory=lambda: os.getenv("SUPPORT_URL", ""))

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///data/bot.db"
        )
    )

    # ── Behaviour ────────────────────────────────────────────────────────────
    NODE_POLL_INTERVAL: int = field(
        default_factory=lambda: int(os.getenv("NODE_POLL_INTERVAL", "45"))
    )
