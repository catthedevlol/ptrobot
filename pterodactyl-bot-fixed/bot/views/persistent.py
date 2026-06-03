"""
views/persistent.py — Persistent Discord UI Views.

These views are registered on startup so buttons keep working
even after the bot restarts.  All custom_id values are static strings
so Discord can match them to the correct handler.
"""

from __future__ import annotations

import discord


# ─────────────────────────────────────────────────────────────────────────────
# Node Status View  (Panel Link + Status Page buttons)
# ─────────────────────────────────────────────────────────────────────────────

class PersistentNodeView(discord.ui.View):
    """
    Attached to every node-status embed.
    Because timeout=None and custom_ids are static, Discord remembers
    these buttons across restarts and routes clicks back here.
    """

    def __init__(self, panel_url: str = "", status_url: str = "") -> None:
        super().__init__(timeout=None)  # ← critical for persistence

        # Build buttons dynamically when URLs are available;
        # on cold-start registration we pass empty strings and buttons are placeholder.
        if panel_url:
            self.add_item(
                discord.ui.Button(
                    label="🖥 Panel",
                    url=panel_url,
                    style=discord.ButtonStyle.link,
                    custom_id="node_panel_link",
                )
            )
        if status_url:
            self.add_item(
                discord.ui.Button(
                    label="📡 Status Page",
                    url=status_url,
                    style=discord.ButtonStyle.link,
                    custom_id="node_status_link",
                )
            )


def build_node_view(panel_url: str, status_url: str) -> discord.ui.View:
    """Factory: create a node view with real URLs for a specific embed."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="🖥 Panel",
            url=panel_url or "https://example.com",
            style=discord.ButtonStyle.link,
        )
    )
    if status_url:
        view.add_item(
            discord.ui.Button(
                label="📡 Status Page",
                url=status_url,
                style=discord.ButtonStyle.link,
            )
        )
    return view


# ─────────────────────────────────────────────────────────────────────────────
# Egg / Software / Plan / Node selection views (used during server creation)
# ─────────────────────────────────────────────────────────────────────────────

class EggSelectView(discord.ui.View):
    """Select an egg (game/runtime) from a dynamic list."""

    def __init__(self, eggs: list[dict], author_id: int) -> None:
        super().__init__(timeout=120)
        self.selected: dict | None = None
        self.author_id = author_id

        options = [
            discord.SelectOption(
                label=egg["name"][:100],
                value=str(egg["id"]),
                description=f"Nest {egg.get('nest', '')} · Egg #{egg['id']}",
            )
            for egg in eggs[:25]  # Discord limit
        ]

        select = discord.ui.Select(
            placeholder="Choose an egg / game type…",
            options=options,
            custom_id="egg_select",
        )
        select.callback = self._on_select
        self.add_item(select)

        # Store egg lookup
        self._eggs = {str(e["id"]): e for e in eggs}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This selection is not for you.", ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        self.selected = self._eggs[value]
        await interaction.response.defer()
        self.stop()


class MinecraftSoftwareView(discord.ui.View):
    """Select Minecraft server software."""

    SOFTWARES = [
        ("Paper", "paper"),
        ("Purpur", "purpur"),
        ("Velocity", "velocity"),
        ("Bungeecord", "bungeecord"),
        ("Waterfall", "waterfall"),
        ("Fabric", "fabric"),
        ("Forge", "forge"),
    ]

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=120)
        self.selected: str | None = None
        self.author_id = author_id

        options = [
            discord.SelectOption(label=name, value=value)
            for name, value in self.SOFTWARES
        ]
        select = discord.ui.Select(
            placeholder="Choose Minecraft software…",
            options=options,
            custom_id="mc_software_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This selection is not for you.", ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected = interaction.data["values"][0]
        await interaction.response.defer()
        self.stop()


class PlanSelectView(discord.ui.View):
    """Select a resource plan."""

    PLANS = {
        "starter": {
            "label": "🌱 Starter",
            "cpu": 100,
            "ram": 2048,
            "disk": 5120,
            "desc": "100% CPU · 2 GB RAM · 5 GB Disk",
        },
        "standard": {
            "label": "⚡ Standard",
            "cpu": 200,
            "ram": 4096,
            "disk": 10240,
            "desc": "200% CPU · 4 GB RAM · 10 GB Disk",
        },
        "extreme": {
            "label": "🔥 Extreme",
            "cpu": 400,
            "ram": 8192,
            "disk": 20480,
            "desc": "400% CPU · 8 GB RAM · 20 GB Disk",
        },
    }

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=120)
        self.selected: dict | None = None
        self.author_id = author_id

        options = [
            discord.SelectOption(
                label=info["label"],
                value=key,
                description=info["desc"],
            )
            for key, info in self.PLANS.items()
        ]
        select = discord.ui.Select(
            placeholder="Choose a plan…",
            options=options,
            custom_id="plan_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This selection is not for you.", ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        key = interaction.data["values"][0]
        self.selected = {"key": key, **self.PLANS[key]}
        await interaction.response.defer()
        self.stop()


class NodeSelectView(discord.ui.View):
    """Select a node from a dynamic list."""

    def __init__(self, nodes: list[dict], author_id: int) -> None:
        super().__init__(timeout=120)
        self.selected: dict | None = None
        self.author_id = author_id

        options = [
            discord.SelectOption(
                label=node["name"][:100],
                value=str(node["id"]),
                description=f"RAM: {node.get('memory', 0)} MB · Disk: {node.get('disk', 0)} MB",
            )
            for node in nodes[:25]
        ]
        select = discord.ui.Select(
            placeholder="Choose a node…",
            options=options,
            custom_id="node_select",
        )
        select.callback = self._on_select
        self.add_item(select)
        self._nodes = {str(n["id"]): n for n in nodes}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This selection is not for you.", ephemeral=True
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        self.selected = self._nodes[value]
        await interaction.response.defer()
        self.stop()


class ConfirmView(discord.ui.View):
    """Simple confirm / cancel view."""

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=60)
        self.confirmed = False
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This button is not for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmed = False
        await interaction.response.defer()
        self.stop()
