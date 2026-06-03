"""
commands/free_hosting_admin.py — Admin commands for free hosting system.

Commands:
- /free-plan-add       — Create/update free hosting plan
- /free-plan-list      — List all plans
- /free-plan-toggle    — Enable/disable plan
- /free-plan-stock     — Adjust stock amount
- /free-claims         — List pending claims
- /free-approve        — Approve a claim
- /free-reject         — Reject a claim
- /free-revoke         — Revoke provisioned server
- /free-provision      — Manual provision trigger
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import BotConfig
from utils.free_hosting import FreeHostingManager, ClaimStatus
from utils.premium_embeds import (
    PremiumEmbedBuilder,
    PremiumColors,
    StatusIndicator,
    create_success_embed,
    create_error_embed,
)
from utils.helpers import require_main_admin, fmt_bytes

log = logging.getLogger("bot.commands.free_hosting_admin")


class FreeHostingAdminCog(commands.Cog):
    """Admin commands for managing free hosting."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = FreeHostingManager(bot.db)

    # ── Plan Management ──────────────────────────────────────────────────

    @app_commands.command(name="free-plan-add")
    @app_commands.describe(
        plan_key="Unique key (e.g., 'minecraft_starter')",
        label="Display name (e.g., 'Starter Minecraft')",
        description="User-facing description",
        cpu_percent="CPU % (e.g., 25)",
        memory_mb="RAM in MB (e.g., 1024)",
        disk_mb="Disk in MB (e.g., 5120)",
        egg_ids="Comma-separated egg IDs (e.g., '1,2,3')",
        node_ids="Comma-separated node IDs (e.g., '1,2')",
        total_stock="Total slots available (e.g., 50)",
    )
    async def free_plan_add(
        self,
        interaction: discord.Interaction,
        plan_key: str,
        label: str,
        description: str,
        cpu_percent: int,
        memory_mb: int,
        disk_mb: int,
        egg_ids: str,
        node_ids: str,
        total_stock: int,
    ) -> None:
        """Create or update a free hosting plan."""
        await interaction.response.defer(ephemeral=True)

        # Check permissions
        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Validate input
        if not plan_key or not plan_key.replace("_", "").isalnum():
            embed = create_error_embed("Invalid Input", "plan_key must be alphanumeric (underscores OK).")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            egg_list = [int(x.strip()) for x in egg_ids.split(",") if x.strip()]
            node_list = [int(x.strip()) for x in node_ids.split(",") if x.strip()]
        except ValueError:
            embed = create_error_embed("Invalid Input", "egg_ids and node_ids must be comma-separated integers.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if not egg_list or not node_list:
            embed = create_error_embed("Invalid Input", "Must specify at least one egg and one node.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Create plan
        plan = await self.manager.create_plan(
            plan_key=plan_key,
            plan_label=label,
            description=description,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            disk_mb=disk_mb,
            egg_ids=egg_list,
            node_ids=node_list,
            total_stock=total_stock,
        )

        # Send success embed
        embed = (
            PremiumEmbedBuilder(
                title=f"Plan Created: {plan.plan_label}",
                color=PremiumColors.SUCCESS,
                icon=StatusIndicator.CHECKMARK,
            )
            .add_section(
                "Details",
                [
                    ("Key", f"`{plan.plan_key}`", True),
                    ("Description", plan.description, False),
                    ("CPU", f"{plan.cpu_percent}%", True),
                    ("RAM", fmt_bytes(plan.memory_mb), True),
                    ("Disk", fmt_bytes(plan.disk_mb), True),
                    ("Eggs", ", ".join(map(str, plan.egg_ids)), False),
                    ("Nodes", ", ".join(map(str, plan.node_ids)), False),
                    ("Total Stock", str(plan.total_stock), True),
                ],
            )
            .set_footer_timestamp()
            .build()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info("Admin %d created plan %s", interaction.user.id, plan_key)

    @app_commands.command(name="free-plan-list")
    @app_commands.describe(
        enabled_only="Show only enabled plans (default: True)",
    )
    async def free_plan_list(
        self,
        interaction: discord.Interaction,
        enabled_only: bool = True,
    ) -> None:
        """List all free hosting plans."""
        await interaction.response.defer(ephemeral=True)

        plans = await self.manager.get_all_plans(enabled_only=enabled_only)

        if not plans:
            embed = create_error_embed(
                "No Plans",
                f"No {'enabled' if enabled_only else ''} plans found.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = PremiumEmbedBuilder(
            title="Free Hosting Plans",
            color=PremiumColors.PRIMARY,
            icon=StatusIndicator.GAUGE,
        )

        for plan in plans:
            status = "✅" if plan.enabled else "❌"
            value = (
                f"{status} {plan.description}\n"
                f"Resources: {plan.cpu_percent}% CPU • {fmt_bytes(plan.memory_mb)} RAM • {fmt_bytes(plan.disk_mb)} Disk\n"
                f"Stock: **{plan.available_count}/{plan.total_stock}** available"
            )
            embed.add_field(f"**{plan.plan_label}** (`{plan.plan_key}`)", value, False)

        embed.set_footer_timestamp()
        await interaction.followup.send(embed=embed.build(), ephemeral=True)

    @app_commands.command(name="free-plan-toggle")
    @app_commands.describe(
        plan_key="Plan key to toggle",
        enabled="Enable or disable",
    )
    async def free_plan_toggle(
        self,
        interaction: discord.Interaction,
        plan_key: str,
        enabled: bool,
    ) -> None:
        """Enable or disable a free hosting plan."""
        await interaction.response.defer(ephemeral=True)

        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        plan = await self.manager.get_plan(plan_key)
        if not plan:
            embed = create_error_embed("Not Found", f"Plan '{plan_key}' does not exist.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if enabled:
            await self.bot.db.enable_stock_plan(plan_key, interaction.user.id)
            embed = create_success_embed(
                "Plan Enabled",
                f"Plan **{plan.plan_label}** is now enabled.",
            )
        else:
            await self.bot.db.disable_stock_plan(plan_key, interaction.user.id)
            embed = create_success_embed(
                "Plan Disabled",
                f"Plan **{plan.plan_label}** is now disabled.",
            )

        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info("Admin %d toggled plan %s to %s", interaction.user.id, plan_key, enabled)

    @app_commands.command(name="free-plan-stock")
    @app_commands.describe(
        plan_key="Plan key",
        new_total="New total stock amount",
    )
    async def free_plan_stock(
        self,
        interaction: discord.Interaction,
        plan_key: str,
        new_total: int,
    ) -> None:
        """Adjust total stock for a plan."""
        await interaction.response.defer(ephemeral=True)

        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        plan = await self.manager.get_plan(plan_key)
        if not plan:
            embed = create_error_embed("Not Found", f"Plan '{plan_key}' does not exist.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Update total_stock
        await self.bot.db.execute(
            "UPDATE free_hosting_stock SET total_stock = ?, available_count = ? WHERE plan_key = ?",
            (new_total, max(0, new_total - plan.claimed_count), plan_key),
        )

        embed = create_success_embed(
            "Stock Updated",
            f"Plan **{plan.plan_label}** stock updated to **{new_total}** slots.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            "Admin %d updated plan %s stock to %d",
            interaction.user.id,
            plan_key,
            new_total,
        )

    # ── Claim Management ──────────────────────────────────────────────────

    @app_commands.command(name="free-claims")
    @app_commands.describe(
        status="Filter by status (pending/approved/provisioned)",
    )
    async def free_claims(
        self,
        interaction: discord.Interaction,
        status: str = "pending",
    ) -> None:
        """List free hosting claims."""
        await interaction.response.defer(ephemeral=True)

        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Get claims by status
        if status == "pending":
            claims = await self.manager.get_pending_claims()
        elif status == "approved":
            claims = await self.manager.get_approved_claims()
        else:
            embed = create_error_embed("Invalid Status", "Use: pending, approved, or provisioned")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if not claims:
            embed = create_error_embed(
                "No Claims",
                f"No {status} claims found.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = PremiumEmbedBuilder(
            title=f"{status.capitalize()} Claims",
            color=PremiumColors.PRIMARY,
            icon=StatusIndicator.GAUGE,
        )

        for claim in claims[:10]:  # Limit to 10 per message
            user_mention = f"<@{claim.discord_id}>"
            value = (
                f"User: {user_mention}\n"
                f"Plan: `{claim.plan_key}`\n"
                f"Claim ID: `{claim.id}`\n"
                f"Requested: <t:{int(claim.claimed_at.timestamp())}:R>"
            )
            embed.add_field(f"Claim #{claim.id}", value, False)

        embed.set_footer_timestamp()
        await interaction.followup.send(embed=embed.build(), ephemeral=True)

    @app_commands.command(name="free-approve")
    @app_commands.describe(
        claim_id="Claim ID to approve",
        notes="Optional approval notes",
    )
    async def free_approve(
        self,
        interaction: discord.Interaction,
        claim_id: int,
        notes: str = "",
    ) -> None:
        """Approve a pending free hosting claim."""
        await interaction.response.defer(ephemeral=True)

        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        claim = await self.bot.db.get_claim(claim_id)
        if not claim:
            embed = create_error_embed("Not Found", f"Claim #{claim_id} does not exist.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        success, message = await self.manager.approve_claim(
            claim_id,
            interaction.user.id,
            notes,
        )

        if success:
            embed = create_success_embed(
                "Claim Approved",
                f"Claim #{claim_id} for <@{claim['discord_id']}> approved.",
            )
        else:
            embed = create_error_embed("Approval Failed", message)

        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info("Admin %d approved claim %d", interaction.user.id, claim_id)

    @app_commands.command(name="free-reject")
    @app_commands.describe(
        claim_id="Claim ID to reject",
        reason="Rejection reason",
    )
    async def free_reject(
        self,
        interaction: discord.Interaction,
        claim_id: int,
        reason: str = "No reason provided",
    ) -> None:
        """Reject a pending free hosting claim."""
        await interaction.response.defer(ephemeral=True)

        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        claim = await self.bot.db.get_claim(claim_id)
        if not claim:
            embed = create_error_embed("Not Found", f"Claim #{claim_id} does not exist.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        success, message = await self.manager.reject_claim(
            claim_id,
            interaction.user.id,
            reason,
        )

        if success:
            embed = create_success_embed(
                "Claim Rejected",
                f"Claim #{claim_id} for <@{claim['discord_id']}> rejected.",
            )
        else:
            embed = create_error_embed("Rejection Failed", message)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Notify user
        try:
            user = await self.bot.fetch_user(claim["discord_id"])
            dm_embed = PremiumEmbedBuilder(
                title="Claim Rejected",
                description=f"Your free hosting claim was rejected.\n\n**Reason:** {reason}",
                color=PremiumColors.ERROR,
                icon=StatusIndicator.CROSS,
            ).set_footer_timestamp().build()
            await user.send(embed=dm_embed)
        except Exception as e:
            log.warning("Could not DM user %d: %s", claim["discord_id"], e)

        log.info("Admin %d rejected claim %d: %s", interaction.user.id, claim_id, reason)

    @app_commands.command(name="free-revoke")
    @app_commands.describe(
        claim_id="Claim ID to revoke",
        reason="Revocation reason",
    )
    async def free_revoke(
        self,
        interaction: discord.Interaction,
        claim_id: int,
        reason: str = "No reason provided",
    ) -> None:
        """Revoke a provisioned free server."""
        await interaction.response.defer(ephemeral=True)

        if not await self._is_admin(interaction.user.id):
            embed = create_error_embed("Permission Denied", "Admin only.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        claim = await self.bot.db.get_claim(claim_id)
        if not claim:
            embed = create_error_embed("Not Found", f"Claim #{claim_id} does not exist.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        success, message = await self.manager.revoke_claim(
            claim_id,
            interaction.user.id,
            reason,
        )

        if success:
            embed = create_success_embed(
                "Claim Revoked",
                f"Claim #{claim_id} for <@{claim['discord_id']}> revoked.",
            )
        else:
            embed = create_error_embed("Revocation Failed", message)

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Notify user
        try:
            user = await self.bot.fetch_user(claim["discord_id"])
            dm_embed = PremiumEmbedBuilder(
                title="Server Revoked",
                description=f"Your free hosted server has been revoked.\n\n**Reason:** {reason}",
                color=PremiumColors.ERROR,
                icon=StatusIndicator.CROSS,
            ).set_footer_timestamp().build()
            await user.send(embed=dm_embed)
        except Exception as e:
            log.warning("Could not DM user %d: %s", claim["discord_id"], e)

        log.info("Admin %d revoked claim %d: %s", interaction.user.id, claim_id, reason)

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin."""
        if user_id in self.bot.config.MAIN_ADMIN_IDS:
            return True
        return await self.bot.db.is_admin(user_id)


async def setup(bot: commands.Bot) -> None:
    """Load the cog."""
    await bot.add_cog(FreeHostingAdminCog(bot))
