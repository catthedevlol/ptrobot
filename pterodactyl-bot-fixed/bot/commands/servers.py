"""
commands/servers.py — Server creation slash command.

Fixes applied:
- Professional success embed with all server details.
- Automatic DM to the server owner with Open Panel + Support buttons.
- All API errors produce clean embeds (no raw tracebacks).
- Missing allocation → descriptive embed error.
- Egg/nest not found handled cleanly.
- API 500/403/timeout handled per-error with hints.
- Server details (node_name, egg_name, software, plan, resources, IP/port)
  saved to DB for future reference.
- Log embed sent to log channel on both success and failure.
"""

from __future__ import annotations

import asyncio
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
    fmt_bytes,
    GREEN,
    BLUE,
    ORANGE,
    RED,
)
from utils.ptero_api import PterodactylAPI, PterodactylError
from views.persistent import (
    EggSelectView,
    MinecraftSoftwareView,
    PlanSelectView,
    NodeSelectView,
    ConfirmView,
)

log = logging.getLogger("bot.cmd.servers")

_STATUS_HINTS: dict[int, str] = {
    0:   "The panel is unreachable. Check your network or panel URL.",
    403: "The API key does not have permission for this action.",
    404: "The requested resource was not found on the panel.",
    422: "The panel rejected the payload — check egg variables or allocation.",
    500: "The panel returned an internal server error. Try again shortly.",
    503: "The panel is temporarily unavailable. Try again shortly.",
}


def _api_error_embed(title: str, exc: PterodactylError) -> discord.Embed:
    hint = _STATUS_HINTS.get(exc.status, "")
    desc = f"**Panel error:** `{exc}`"
    if hint:
        desc += f"\n\n💡 {hint}"
    return discord.Embed(title=f"❌ {title}", description=desc, colour=RED)


class ServerCommands(commands.Cog, name="Servers"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _api(self) -> PterodactylAPI:
        return PterodactylAPI(
            self.bot.config.PANEL_URL,
            self.bot.config.PANEL_API_KEY,
        )

    # ── /create-server ────────────────────────────────────────────────────────

    @app_commands.command(
        name="create-server",
        description="Create a Pterodactyl game server for a Discord member.",
    )
    @app_commands.describe(member="The Discord member to create a server for.")
    @require_any_admin()
    async def create_server(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        db = self.bot.db
        api = self._api()

        # ── Step 1: verify panel account ─────────────────────────────────────
        ptero_user = await db.get_ptero_user(member.id)
        if not ptero_user:
            await interaction.followup.send(
                embed=error_embed(
                    "No Panel Account",
                    (
                        f"{member.mention} does not have a Pterodactyl account yet.\n\n"
                        "Please run `/create-user` for this member first, then try again."
                    ),
                ),
                ephemeral=True,
            )
            await api.close()
            return

        # ── Step 2: egg selection ─────────────────────────────────────────────
        try:
            nests = await api.list_nests()
        except PterodactylError as exc:
            await interaction.followup.send(
                embed=_api_error_embed("Could Not Fetch Nests", exc),
                ephemeral=True,
            )
            await api.close()
            return

        if not nests:
            await interaction.followup.send(
                embed=error_embed("No Nests Found", "The panel returned no nest configurations."),
                ephemeral=True,
            )
            await api.close()
            return

        all_eggs: list[dict] = []
        for nest in nests:
            try:
                eggs = await api.list_eggs(nest["id"])
                for egg in eggs:
                    egg["nest_name"] = nest.get("name", "")
                    egg["nest"] = nest["id"]
                all_eggs.extend(eggs)
            except PterodactylError as exc:
                log.warning("Could not fetch eggs for nest %d: %s", nest["id"], exc)

        if not all_eggs:
            await interaction.followup.send(
                embed=error_embed(
                    "No Eggs Found",
                    "The panel returned no egg configurations. "
                    "Please check your nests in the panel.",
                ),
                ephemeral=True,
            )
            await api.close()
            return

        egg_view = EggSelectView(all_eggs, interaction.user.id)
        await interaction.followup.send(
            embed=info_embed(
                "Step 2/6 — Select Game Type",
                f"Choose the egg (game/runtime) for **{member.display_name}**'s server.",
            ),
            view=egg_view,
            ephemeral=True,
        )

        if not await _wait_for_view(egg_view):
            await interaction.edit_original_response(
                embed=error_embed("Timed Out", "Server creation cancelled — no egg selected."),
                view=None,
            )
            await api.close()
            return

        selected_egg = egg_view.selected

        # ── Step 3: Minecraft software (conditional) ──────────────────────────
        mc_software: str | None = None
        if "minecraft" in selected_egg["name"].lower():
            sw_view = MinecraftSoftwareView(interaction.user.id)
            await interaction.edit_original_response(
                embed=info_embed(
                    "Step 3/6 — Select Minecraft Software",
                    "Choose the server software for this Minecraft instance.",
                ),
                view=sw_view,
            )
            if not await _wait_for_view(sw_view):
                await interaction.edit_original_response(
                    embed=error_embed("Timed Out", "Server creation cancelled — no software selected."),
                    view=None,
                )
                await api.close()
                return
            mc_software = sw_view.selected

        # ── Step 4: plan selection ─────────────────────────────────────────────
        plan_view = PlanSelectView(interaction.user.id)
        await interaction.edit_original_response(
            embed=info_embed("Step 4/6 — Select a Plan", "Choose the resource plan for this server."),
            view=plan_view,
        )
        if not await _wait_for_view(plan_view):
            await interaction.edit_original_response(
                embed=error_embed("Timed Out", "Server creation cancelled — no plan selected."),
                view=None,
            )
            await api.close()
            return

        plan = plan_view.selected

        # ── Step 5: node selection ─────────────────────────────────────────────
        try:
            nodes = await api.list_nodes()
        except PterodactylError as exc:
            await interaction.edit_original_response(
                embed=_api_error_embed("Could Not Fetch Nodes", exc), view=None,
            )
            await api.close()
            return

        if not nodes:
            await interaction.edit_original_response(
                embed=error_embed("No Nodes Found", "The panel returned no nodes."),
                view=None,
            )
            await api.close()
            return

        node_view = NodeSelectView(nodes, interaction.user.id)
        await interaction.edit_original_response(
            embed=info_embed("Step 5/6 — Select a Node", "Choose the node to deploy this server on."),
            view=node_view,
        )
        if not await _wait_for_view(node_view):
            await interaction.edit_original_response(
                embed=error_embed("Timed Out", "Server creation cancelled — no node selected."),
                view=None,
            )
            await api.close()
            return

        target_node = node_view.selected

        # ── Step 6: confirm ────────────────────────────────────────────────────
        confirm_embed = discord.Embed(title="Step 6/6 — Confirm Server Creation", colour=ORANGE)
        confirm_embed.add_field(name="👤 For", value=member.mention, inline=True)
        confirm_embed.add_field(name="🥚 Egg", value=selected_egg["name"], inline=True)
        if mc_software:
            confirm_embed.add_field(name="⚙️ Software", value=mc_software.title(), inline=True)
        confirm_embed.add_field(name="📋 Plan", value=plan["label"], inline=True)
        confirm_embed.add_field(name="🖥 Node", value=target_node["name"], inline=True)
        confirm_embed.add_field(
            name="💾 Resources",
            value=f"CPU: {plan['cpu']}%  •  RAM: {fmt_bytes(plan['ram'])}  •  Disk: {fmt_bytes(plan['disk'])}",
            inline=False,
        )

        confirm_view = ConfirmView(interaction.user.id)
        await interaction.edit_original_response(embed=confirm_embed, view=confirm_view)
        await _wait_for_view(confirm_view, timeout=60.0)

        if not confirm_view.confirmed:
            await interaction.edit_original_response(
                embed=error_embed("Cancelled", "Server creation was cancelled."), view=None,
            )
            await api.close()
            return

        await interaction.edit_original_response(
            embed=info_embed("Creating Server…", "Please wait while the server is being set up."),
            view=None,
        )

        # ── Find free allocation ───────────────────────────────────────────────
        try:
            allocation = await api.get_free_allocation(target_node["id"])
        except PterodactylError as exc:
            await interaction.edit_original_response(
                embed=_api_error_embed("Allocation Fetch Failed", exc), view=None,
            )
            await api.close()
            return

        if not allocation:
            await interaction.edit_original_response(
                embed=error_embed(
                    "No Free Allocations",
                    (
                        f"There are no free allocations on **{target_node['name']}**.\n\n"
                        "Please add allocations in the Pterodactyl panel, or choose a different node."
                    ),
                ),
                view=None,
            )
            await api.close()
            return

        # ── Build environment from egg variables ───────────────────────────────
        environment: dict[str, str] = {}
        for var in (
            selected_egg.get("relationships", {})
            .get("variables", {})
            .get("data", [])
        ):
            attrs = var.get("attributes", {})
            env_var = attrs.get("env_variable", "")
            default = attrs.get("default_value", "")
            if env_var:
                environment[env_var] = default

        if mc_software and "SERVER_JARFILE" in environment:
            environment["SERVER_JARFILE"] = f"{mc_software}.jar"

        # ── Create server ──────────────────────────────────────────────────────
        safe_name = member.display_name[:20].replace(" ", "-")
        egg_short = selected_egg["name"][:15].replace(" ", "-")
        server_name = f"{safe_name}-{egg_short}"

        try:
            server = await api.create_server(
                name=server_name,
                user_id=ptero_user["ptero_id"],
                egg_id=selected_egg["id"],
                docker_image=selected_egg.get("docker_image", ""),
                startup=selected_egg.get("startup", ""),
                environment=environment,
                limits={
                    "memory": plan["ram"],
                    "swap": 0,
                    "disk": plan["disk"],
                    "io": 500,
                    "cpu": plan["cpu"],
                },
                feature_limits={"databases": 2, "backups": 2, "allocations": 1},
                allocation_id=allocation["id"],
            )
        except PterodactylError as exc:
            log.error("Server creation failed for %s: %s", member, exc)
            await interaction.edit_original_response(
                embed=_api_error_embed("Server Creation Failed", exc), view=None,
            )
            await self._push_log(
                interaction,
                action="Failed Server Creation",
                details=(
                    f"**Owner:** {member.mention}\n"
                    f"**Node:** {target_node['name']}\n"
                    f"**Egg:** {selected_egg['name']}\n"
                    f"**Error:** `{exc}`"
                ),
            )
            await api.close()
            return
        finally:
            await api.close()

        # Allocation details for display
        alloc_ip = allocation.get("ip", "")
        alloc_port = allocation.get("port", 0)
        alloc_alias = allocation.get("ip_alias") or alloc_ip

        # ── Save to DB ─────────────────────────────────────────────────────────
        await db.create_ptero_server(
            discord_id=member.id,
            ptero_server_id=server["id"],
            identifier=server.get("identifier", ""),
            name=server_name,
            egg_id=selected_egg["id"],
            node_id=target_node["id"],
            created_by=interaction.user.id,
            node_name=target_node["name"],
            egg_name=selected_egg["name"],
            software=mc_software or "",
            plan_label=plan["label"],
            cpu=plan["cpu"],
            ram=plan["ram"],
            disk=plan["disk"],
            allocation_ip=alloc_alias,
            allocation_port=alloc_port,
        )

        # ── Success embed ──────────────────────────────────────────────────────
        server_url = f"{self.bot.config.PANEL_URL}/server/{server.get('identifier','')}"

        success = discord.Embed(
            title="✅ Server Created",
            colour=GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        success.add_field(name="👤 Owner", value=member.mention, inline=True)
        success.add_field(name="🏷 Server Name", value=f"`{server_name}`", inline=True)
        success.add_field(name="🖥 Node", value=target_node["name"], inline=True)
        success.add_field(name="🥚 Egg", value=selected_egg["name"], inline=True)
        if mc_software:
            success.add_field(name="⚙️ Software", value=mc_software.title(), inline=True)
        success.add_field(name="🆔 Server ID", value=str(server["id"]), inline=True)
        success.add_field(
            name="💾 Resources",
            value=(
                f"CPU: **{plan['cpu']}%**\n"
                f"RAM: **{fmt_bytes(plan['ram'])}**\n"
                f"Disk: **{fmt_bytes(plan['disk'])}**"
            ),
            inline=True,
        )
        if alloc_alias and alloc_port:
            success.add_field(
                name="🌐 Connection",
                value=f"`{alloc_alias}:{alloc_port}`",
                inline=True,
            )
        success.set_footer(text=f"Created by {interaction.user}")

        panel_view = discord.ui.View(timeout=None)
        panel_view.add_item(
            discord.ui.Button(
                label="🖥 Open Panel",
                url=server_url,
                style=discord.ButtonStyle.link,
            )
        )

        await interaction.edit_original_response(embed=success, view=panel_view)

        # ── DM the server owner ────────────────────────────────────────────────
        dm_embed = discord.Embed(
            title="🎮 Your Server Is Ready!",
            colour=GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.add_field(name="🏷 Server Name", value=f"`{server_name}`", inline=True)
        dm_embed.add_field(name="🖥 Node", value=target_node["name"], inline=True)
        dm_embed.add_field(name="🥚 Egg", value=selected_egg["name"], inline=True)
        if mc_software:
            dm_embed.add_field(name="⚙️ Software", value=mc_software.title(), inline=True)
        dm_embed.add_field(name="👤 Panel Username", value=f"`{ptero_user['username']}`", inline=True)
        if alloc_alias and alloc_port:
            dm_embed.add_field(
                name="🌐 Address / Port",
                value=f"`{alloc_alias}:{alloc_port}`",
                inline=True,
            )
        dm_embed.add_field(
            name="🔗 Panel Link",
            value=f"[Click here to manage your server]({server_url})",
            inline=False,
        )
        dm_embed.set_footer(text="Need help? Open a support ticket in the Discord server.")

        dm_view = discord.ui.View(timeout=None)
        dm_view.add_item(
            discord.ui.Button(
                label="🖥 Open Panel",
                url=server_url,
                style=discord.ButtonStyle.link,
            )
        )
        if self.bot.config.SUPPORT_URL:
            dm_view.add_item(
                discord.ui.Button(
                    label="💬 Support",
                    url=self.bot.config.SUPPORT_URL,
                    style=discord.ButtonStyle.link,
                )
            )

        dm_sent = True
        try:
            await member.send(embed=dm_embed, view=dm_view)
        except discord.Forbidden:
            dm_sent = False
            log.warning("Could not DM server info to %s — DMs disabled", member)

        # ── Log ────────────────────────────────────────────────────────────────
        await self._push_log(
            interaction,
            action="Server Created",
            details=(
                f"**Owner:** {member.mention} (`{member.id}`)\n"
                f"**Server:** `{server_name}` (ID: `{server['id']}`)\n"
                f"**Node:** {target_node['name']}\n"
                f"**Egg:** {selected_egg['name']}"
                + (f"\n**Software:** {mc_software.title()}" if mc_software else "") +
                f"\n**Plan:** {plan['label']}\n"
                f"**Resources:** CPU {plan['cpu']}% · RAM {fmt_bytes(plan['ram'])} · Disk {fmt_bytes(plan['disk'])}\n"
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
            log.error("Log push failed: %s", exc)


# ── Helper ─────────────────────────────────────────────────────────────────────

async def _wait_for_view(view: discord.ui.View, timeout: float = 120.0) -> bool:
    try:
        await asyncio.wait_for(view.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerCommands(bot))
