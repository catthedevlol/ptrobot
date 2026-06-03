"""
utils/free_hosting.py — Free hosting claim management and provisioning.

Handles:
- Claim creation with duplicate prevention
- Account validation before provisioning
- Automated server provisioning
- Stock management
- Anti-abuse tracking
- Provisioning failure recovery
"""

import logging
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional
from enum import Enum

log = logging.getLogger("bot.free_hosting")


class ClaimStatus(str, Enum):
    """Claim lifecycle states."""
    PENDING = "pending"           # Awaiting admin review
    APPROVED = "approved"         # Admin approved, ready to provision
    PROVISIONED = "provisioned"   # Server created, credentials issued
    REJECTED = "rejected"         # Admin rejected claim
    REVOKED = "revoked"           # Claimed server removed


class ValidationStatus(str, Enum):
    """Validation check results."""
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"


@dataclass
class StockPlan:
    """Free hosting plan with resource limits."""
    plan_key: str
    plan_label: str
    description: str
    cpu_percent: int
    memory_mb: int
    disk_mb: int
    egg_ids: list[int]
    node_ids: list[int]
    total_stock: int
    claimed_count: int = 0
    available_count: int = 0
    enabled: bool = True

    @property
    def is_available(self) -> bool:
        """Plan has stock and is enabled."""
        return self.enabled and self.available_count > 0

    def to_dict(self) -> dict:
        return {
            "plan_key": self.plan_key,
            "plan_label": self.plan_label,
            "description": self.description,
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "disk_mb": self.disk_mb,
            "egg_ids": self.egg_ids,
            "node_ids": self.node_ids,
            "total_stock": self.total_stock,
            "claimed_count": self.claimed_count,
            "available_count": self.available_count,
            "enabled": self.enabled,
        }


@dataclass
class UserClaim:
    """A user's free hosting claim."""
    id: int
    discord_id: int
    plan_key: str
    status: ClaimStatus
    claimed_at: str  # ISO-8601 UTC
    approved_at: Optional[str] = None
    approved_by: Optional[int] = None
    provisioned_at: Optional[str] = None
    provisioned_by: Optional[int] = None
    pterodactyl_user_id: Optional[int] = None
    server_id: Optional[int] = None
    rejection_reason: Optional[str] = None
    revoked_at: Optional[str] = None
    revoked_by: Optional[int] = None
    notes: Optional[str] = None

    @property
    def is_active(self) -> bool:
        """Claim is active (not rejected or revoked)."""
        return self.status not in (ClaimStatus.REJECTED, ClaimStatus.REVOKED)

    @property
    def is_provisioned(self) -> bool:
        """Server has been provisioned."""
        return self.status == ClaimStatus.PROVISIONED and self.server_id is not None


class FreeHostingManager:
    """Manages free hosting claims, stock, and provisioning."""

    def __init__(self, db):
        self.db = db

    async def create_plan(
        self,
        plan_key: str,
        plan_label: str,
        description: str,
        cpu_percent: int,
        memory_mb: int,
        disk_mb: int,
        egg_ids: list[int],
        node_ids: list[int],
        total_stock: int,
    ) -> StockPlan:
        """
        Create or update a free hosting plan.

        Args:
            plan_key: Unique identifier (e.g., 'minecraft_starter')
            plan_label: Display name (e.g., 'Starter Minecraft')
            description: User-facing description
            cpu_percent: CPU allocation %
            memory_mb: RAM in MB
            disk_mb: Disk in MB
            egg_ids: List of compatible egg IDs
            node_ids: List of nodes to provision on
            total_stock: Total slots available

        Returns:
            Created StockPlan
        """
        await self.db.create_stock_plan(
            plan_key=plan_key,
            plan_label=plan_label,
            description=description,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            disk_mb=disk_mb,
            egg_ids=egg_ids,
            node_ids=node_ids,
            total_stock=total_stock,
        )

        return await self.get_plan(plan_key)

    async def get_plan(self, plan_key: str) -> Optional[StockPlan]:
        """Fetch a plan by key."""
        row = await self.db.get_stock_plan(plan_key)
        if not row:
            return None

        return StockPlan(
            plan_key=row["plan_key"],
            plan_label=row["plan_label"],
            description=row["description"],
            cpu_percent=row["cpu_percent"],
            memory_mb=row["memory_mb"],
            disk_mb=row["disk_mb"],
            egg_ids=json.loads(row["egg_ids"]),
            node_ids=json.loads(row["node_ids"]),
            total_stock=row["total_stock"],
            claimed_count=row["claimed_count"],
            available_count=row["available_count"],
            enabled=bool(row["enabled"]),
        )

    async def get_all_plans(self, enabled_only: bool = False) -> list[StockPlan]:
        """Fetch all plans."""
        rows = await self.db.get_all_stock_plans(enabled_only=enabled_only)
        return [
            StockPlan(
                plan_key=row["plan_key"],
                plan_label=row["plan_label"],
                description=row["description"],
                cpu_percent=row["cpu_percent"],
                memory_mb=row["memory_mb"],
                disk_mb=row["disk_mb"],
                egg_ids=json.loads(row["egg_ids"]),
                node_ids=json.loads(row["node_ids"]),
                total_stock=row["total_stock"],
                claimed_count=row["claimed_count"],
                available_count=row["available_count"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    async def request_free_server(
        self,
        discord_id: int,
        plan_key: str,
    ) -> tuple[bool, str, Optional[int]]:
        """
        Request a free server.

        Validates:
        - User hasn't already claimed this plan
        - Stock is available
        - Plan is enabled

        Returns:
            (success, message, claim_id)
        """
        # Check for existing claim (active or pending)
        existing = await self.db.get_user_claim(discord_id, plan_key)
        if existing:
            if existing["status"] == ClaimStatus.PROVISIONED.value:
                return False, "You already have a server provisioned on this plan.", None
            elif existing["status"] == ClaimStatus.APPROVED.value:
                return (
                    False,
                    "Your claim is pending approval. Please wait for an admin to review it.",
                    None,
                )
            elif existing["status"] == ClaimStatus.PENDING.value:
                return (
                    False,
                    "You already have a pending request for this plan.",
                    None,
                )

        # Check plan exists and is enabled
        plan = await self.get_plan(plan_key)
        if not plan:
            return False, f"Plan '{plan_key}' does not exist.", None

        if not plan.enabled:
            return False, f"Plan '{plan.plan_label}' is currently disabled.", None

        # Check stock
        if not plan.is_available:
            return (
                False,
                f"Plan '{plan.plan_label}' is out of stock. Please check back later.",
                None,
            )

        # Create claim (will be PENDING by default)
        claim_id = await self.db.create_claim(discord_id, plan_key)
        if claim_id is None:
            return False, "Could not create claim (duplicate detected).", None

        log.info(
            "Created pending claim %d for user %d on plan %s",
            claim_id,
            discord_id,
            plan_key,
        )

        return (
            True,
            f"Claim submitted! Please wait for an admin to review your request.",
            claim_id,
        )

    async def validate_account(
        self,
        claim_id: int,
        discord_id: int,
        plan_key: str,
    ) -> tuple[bool, str]:
        """
        Validate that user account exists in Pterodactyl.

        This check happens before provisioning.

        Returns:
            (success, message)
        """
        ptero_user = await self.db.get_ptero_user(discord_id)
        if not ptero_user:
            reason = "No Pterodactyl account found. Create one with /ptero-user"
            await self.db.log_validation(
                claim_id,
                discord_id,
                plan_key,
                validation_type="account_check",
                status=ValidationStatus.FAIL.value,
                details=reason,
            )
            return False, reason

        await self.db.log_validation(
            claim_id,
            discord_id,
            plan_key,
            validation_type="account_check",
            status=ValidationStatus.PASS.value,
            details=f"ptero_id={ptero_user['ptero_id']}",
        )
        return True, ""

    async def approve_claim(
        self,
        claim_id: int,
        admin_id: int,
        notes: str = "",
    ) -> tuple[bool, str]:
        """
        Admin approves a claim (moves to APPROVED).

        Still requires manual provisioning or auto-provisioning trigger.

        Returns:
            (success, message)
        """
        success = await self.db.approve_claim(claim_id, admin_id, notes)
        if success:
            return True, "Claim approved."
        return False, "Could not approve claim (wrong state)."

    async def reject_claim(
        self,
        claim_id: int,
        admin_id: int,
        reason: str = "No reason provided",
    ) -> tuple[bool, str]:
        """
        Admin rejects a claim (moves to REJECTED).

        Returns:
            (success, message)
        """
        success = await self.db.reject_claim(claim_id, admin_id, reason)
        if success:
            return True, "Claim rejected."
        return False, "Could not reject claim (wrong state)."

    async def provision_server(
        self,
        claim_id: int,
        discord_id: int,
        plan_key: str,
        pterodactyl_user_id: int,
        pterodactyl_server_id: int,
        server_name: str,
        provisioned_by: int,
    ) -> tuple[bool, str]:
        """
        Mark a claim as provisioned after server creation.

        MUST be called after server is successfully created in Pterodactyl.

        Args:
            claim_id: The claim ID
            discord_id: Discord user ID
            plan_key: Plan key
            pterodactyl_user_id: Pterodactyl user ID
            pterodactyl_server_id: Newly created server ID
            server_name: Server display name
            provisioned_by: Admin or system ID who provisioned

        Returns:
            (success, message)
        """
        claim = await self.db.get_claim(claim_id)
        if not claim:
            return False, "Claim not found."

        if claim["status"] != ClaimStatus.APPROVED.value:
            return False, f"Claim is in {claim['status']} state, cannot provision."

        # Mark claim as provisioned
        await self.db.mark_claim_provisioned(
            claim_id,
            pterodactyl_user_id,
            pterodactyl_server_id,
            provisioned_by,
        )

        # Record provisioned server
        await self.db.record_provisioned_server(
            claim_id=claim_id,
            discord_id=discord_id,
            plan_key=plan_key,
            pterodactyl_server_id=pterodactyl_server_id,
            server_name=server_name,
            provisioned_by=provisioned_by,
        )

        # Update stock
        plan = await self.get_plan(plan_key)
        if plan:
            claimed_count = plan.claimed_count + 1
            await self.db.update_stock_availability(plan_key, claimed_count)

        log.info(
            "Provisioned claim %d: user=%d server=%d",
            claim_id,
            discord_id,
            pterodactyl_server_id,
        )

        return True, "Server provisioned successfully."

    async def revoke_claim(
        self,
        claim_id: int,
        admin_id: int,
        reason: str = "No reason provided",
    ) -> tuple[bool, str]:
        """
        Revoke a provisioned claim (free server removal).

        NOTE: Does NOT delete the server from Pterodactyl.
        Admin must delete the server manually if desired.

        Returns:
            (success, message)
        """
        claim = await self.db.get_claim(claim_id)
        if not claim:
            return False, "Claim not found."

        if claim["status"] == ClaimStatus.PROVISIONED.value:
            # Decrement stock when revoking
            plan = await self.get_plan(claim["plan_key"])
            if plan and plan.claimed_count > 0:
                claimed_count = plan.claimed_count - 1
                await self.db.update_stock_availability(claim["plan_key"], claimed_count)

        success = await self.db.revoke_claim(claim_id, admin_id, reason)
        if success:
            return True, "Claim revoked."
        return False, "Could not revoke claim."

    async def get_user_claims(self, discord_id: int) -> list[UserClaim]:
        """Fetch all claims for a user."""
        rows = await self.db.get_user_claims(discord_id)
        return [
            UserClaim(
                id=row["id"],
                discord_id=row["discord_id"],
                plan_key=row["plan_key"],
                status=ClaimStatus(row["status"]),
                claimed_at=row["claimed_at"],
                approved_at=row["approved_at"],
                approved_by=row["approved_by"],
                provisioned_at=row["provisioned_at"],
                provisioned_by=row["provisioned_by"],
                pterodactyl_user_id=row["pterodactyl_user_id"],
                server_id=row["server_id"],
                rejection_reason=row["rejection_reason"],
                revoked_at=row["revoked_at"],
                revoked_by=row["revoked_by"],
                notes=row["notes"],
            )
            for row in rows
        ]

    async def get_pending_claims(self) -> list[UserClaim]:
        """Fetch all pending claims (awaiting admin review)."""
        rows = await self.db.get_claims_by_status(ClaimStatus.PENDING.value)
        return [
            UserClaim(
                id=row["id"],
                discord_id=row["discord_id"],
                plan_key=row["plan_key"],
                status=ClaimStatus(row["status"]),
                claimed_at=row["claimed_at"],
                approved_at=row["approved_at"],
                approved_by=row["approved_by"],
                provisioned_at=row["provisioned_at"],
                provisioned_by=row["provisioned_by"],
                pterodactyl_user_id=row["pterodactyl_user_id"],
                server_id=row["server_id"],
                rejection_reason=row["rejection_reason"],
                revoked_at=row["revoked_at"],
                revoked_by=row["revoked_by"],
                notes=row["notes"],
            )
            for row in rows
        ]

    async def get_approved_claims(self) -> list[UserClaim]:
        """Fetch all approved claims (ready to provision)."""
        rows = await self.db.get_claims_by_status(ClaimStatus.APPROVED.value)
        return [
            UserClaim(
                id=row["id"],
                discord_id=row["discord_id"],
                plan_key=row["plan_key"],
                status=ClaimStatus(row["status"]),
                claimed_at=row["claimed_at"],
                approved_at=row["approved_at"],
                approved_by=row["approved_by"],
                provisioned_at=row["provisioned_at"],
                provisioned_by=row["provisioned_by"],
                pterodactyl_user_id=row["pterodactyl_user_id"],
                server_id=row["server_id"],
                rejection_reason=row["rejection_reason"],
                revoked_at=row["revoked_at"],
                revoked_by=row["revoked_by"],
                notes=row["notes"],
            )
            for row in rows
        ]

    async def auto_provision_pending(
        self,
        provisioning_callback,
        system_id: int = 0,
    ) -> dict:
        """
        Auto-provision all approved claims.

        Callback signature: async def provision_callback(claim: UserClaim) -> (success: bool, message: str)

        Args:
            provisioning_callback: Async function to provision each claim
            system_id: ID of system/bot performing provisioning

        Returns:
            Summary dict with success/fail counts
        """
        approved = await self.get_approved_claims()
        stats = {"total": len(approved), "provisioned": 0, "failed": 0, "errors": []}

        for claim in approved:
            try:
                success, message = await provisioning_callback(claim)
                if success:
                    stats["provisioned"] += 1
                    log.info("Auto-provisioned claim %d", claim.id)
                else:
                    stats["failed"] += 1
                    stats["errors"].append({"claim_id": claim.id, "error": message})
                    log.warning("Auto-provision failed for claim %d: %s", claim.id, message)
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append({"claim_id": claim.id, "error": str(e)})
                log.error("Auto-provision exception for claim %d: %s", claim.id, e, exc_info=True)

        return stats
