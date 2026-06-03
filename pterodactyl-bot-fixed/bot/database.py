"""
database.py — Enhanced async database layer with free hosting claims system.

Tables added:
- free_hosting_claims    — Per-user claim tracking
- free_hosting_stock     — Server type inventory
- claim_validation_logs  — Claim validation audit trail
- provisioned_servers    — Auto-provisioned server records
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

log = logging.getLogger("bot.database")


# ────────────────────────────────────────────────────────────────
# DDL — all tables created with IF NOT EXISTS so migrations are safe
# ────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    discord_id  INTEGER PRIMARY KEY,
    added_by    INTEGER NOT NULL,
    added_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS pterodactyl_users (
    discord_id      INTEGER PRIMARY KEY,
    ptero_id        INTEGER NOT NULL,
    username        TEXT    NOT NULL,
    email           TEXT    NOT NULL,
    password        TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pterodactyl_servers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      INTEGER NOT NULL,
    ptero_server_id INTEGER NOT NULL,
    identifier      TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    egg_id          INTEGER NOT NULL,
    node_id         INTEGER NOT NULL,
    node_name       TEXT    NOT NULL DEFAULT '',
    egg_name        TEXT    NOT NULL DEFAULT '',
    software        TEXT    NOT NULL DEFAULT '',
    plan_label      TEXT    NOT NULL DEFAULT '',
    cpu             INTEGER NOT NULL DEFAULT 0,
    ram             INTEGER NOT NULL DEFAULT 0,
    disk            INTEGER NOT NULL DEFAULT 0,
    allocation_ip   TEXT    NOT NULL DEFAULT '',
    allocation_port INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS node_status_messages (
    node_id     INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    PRIMARY KEY (node_id, channel_id)
);

CREATE TABLE IF NOT EXISTS node_uptime_messages (
    channel_id  INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_messages (
    node_id     INTEGER PRIMARY KEY,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_channels (
    channel_id  INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS node_downtime_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    node_name   TEXT    NOT NULL,
    down_at     TEXT    NOT NULL,
    up_at       TEXT,
    duration_s  INTEGER
);

CREATE TABLE IF NOT EXISTS node_uptime (
    node_id                 INTEGER PRIMARY KEY,
    node_name               TEXT    NOT NULL DEFAULT '',
    online_since            TEXT,
    offline_since           TEXT,
    last_online_session_s   INTEGER NOT NULL DEFAULT 0,
    last_downtime_s         INTEGER NOT NULL DEFAULT 0,
    total_downtime_s        INTEGER NOT NULL DEFAULT 0,
    month_start             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-01T00:00:00Z','now')),
    month_downtime_s        INTEGER NOT NULL DEFAULT 0,
    last_seen_online        TEXT,
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS node_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    node_name   TEXT    NOT NULL,
    event       TEXT    NOT NULL CHECK (event IN ('online','offline')),
    ts          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS node_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER NOT NULL,
    node_name       TEXT    NOT NULL,
    month           TEXT    NOT NULL,
    uptime_pct      REAL    NOT NULL,
    total_downtime_s INTEGER NOT NULL,
    recorded_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (node_id, month)
);

CREATE TABLE IF NOT EXISTS server_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id   INTEGER NOT NULL,
    discord_id  INTEGER NOT NULL,
    event       TEXT    NOT NULL,
    details     TEXT,
    ts          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS action_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT    NOT NULL,
    actor_id    INTEGER,
    target_id   INTEGER,
    details     TEXT,
    ts          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS log_channel (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    channel_id  INTEGER NOT NULL
);

-- ────────────────────────────────────────────────────────────────
-- Free Hosting Claims System
-- ────────────────────────────────────────────────────────────────

-- Server type inventory and allocation
CREATE TABLE IF NOT EXISTS free_hosting_stock (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_key            TEXT    NOT NULL UNIQUE,  -- e.g., 'starter', 'standard'
    plan_label          TEXT    NOT NULL,         -- e.g., 'Starter Plan'
    description         TEXT    NOT NULL,         -- User-friendly description
    cpu_percent         INTEGER NOT NULL,
    memory_mb           INTEGER NOT NULL,
    disk_mb             INTEGER NOT NULL,
    egg_ids             TEXT    NOT NULL,         -- JSON list of egg IDs
    node_ids            TEXT    NOT NULL,         -- JSON list of node IDs
    total_stock         INTEGER NOT NULL,         -- Total slots available
    claimed_count       INTEGER NOT NULL DEFAULT 0, -- Number of claims
    available_count     INTEGER NOT NULL DEFAULT 0, -- total_stock - claimed_count
    enabled             BOOLEAN NOT NULL DEFAULT 1,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- User claims (one per user per plan type)
CREATE TABLE IF NOT EXISTS free_hosting_claims (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id          INTEGER NOT NULL,
    plan_key            TEXT    NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('pending', 'approved', 'provisioned', 'rejected', 'revoked')),
    rejection_reason    TEXT,
    pterodactyl_user_id INTEGER,           -- Link to ptero user after validation
    server_id           INTEGER,            -- Link to provisioned server
    claimed_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    approved_at         TEXT,
    approved_by         INTEGER,            -- Admin who approved
    provisioned_at      TEXT,
    provisioned_by      INTEGER,            -- Admin/system who provisioned
    revoked_at          TEXT,
    revoked_by          INTEGER,
    notes               TEXT,
    UNIQUE (discord_id, plan_key)
);

-- Validation audit trail
CREATE TABLE IF NOT EXISTS claim_validation_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id        INTEGER NOT NULL,
    discord_id      INTEGER NOT NULL,
    plan_key        TEXT    NOT NULL,
    validation_type TEXT    NOT NULL,  -- 'account_check', 'email_verify', 'manual_review'
    status          TEXT    NOT NULL,  -- 'pass', 'fail', 'pending'
    details         TEXT,
    validated_by    INTEGER,
    validated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (claim_id) REFERENCES free_hosting_claims(id)
);

-- Provisioned server records (audit trail for auto-provisioning)
CREATE TABLE IF NOT EXISTS provisioned_servers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id        INTEGER NOT NULL UNIQUE,
    discord_id      INTEGER NOT NULL,
    plan_key        TEXT    NOT NULL,
    pterodactyl_server_id INTEGER NOT NULL,
    server_name     TEXT    NOT NULL,
    credentials_issued BOOLEAN NOT NULL DEFAULT 0,
    provisioned_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    provisioned_by  INTEGER NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES free_hosting_claims(id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_claims_discord_id ON free_hosting_claims(discord_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON free_hosting_claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_plan_key ON free_hosting_claims(plan_key);
CREATE INDEX IF NOT EXISTS idx_validation_claim_id ON claim_validation_logs(claim_id);
CREATE INDEX IF NOT EXISTS idx_provisioned_discord_id ON provisioned_servers(discord_id);
CREATE INDEX IF NOT EXISTS idx_stock_plan_key ON free_hosting_stock(plan_key);
"""

# Migration: add health check probe tracking
MIGRATIONS = [
    "ALTER TABLE pterodactyl_servers ADD COLUMN node_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE pterodactyl_servers ADD COLUMN egg_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE pterodactyl_servers ADD COLUMN software TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE pterodactyl_servers ADD COLUMN plan_label TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE pterodactyl_servers ADD COLUMN cpu INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE pterodactyl_servers ADD COLUMN ram INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE pterodactyl_servers ADD COLUMN disk INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE pterodactyl_servers ADD COLUMN allocation_ip TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE pterodactyl_servers ADD COLUMN allocation_port INTEGER NOT NULL DEFAULT 0",
]


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite+aiosqlite:///"):
            self._path = url[len("sqlite+aiosqlite:///"):]
        elif url.startswith("sqlite:///"):
            self._path = url[len("sqlite:///"):]
        else:
            self._path = url
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Open the connection, apply schema, run migrations."""
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._run_migrations()
        log.info("Database initialised at %s", self._path)

    async def _run_migrations(self) -> None:
        """Apply any ALTER TABLE migrations idempotently."""
        for stmt in MIGRATIONS:
            try:
                await self._db.execute(stmt)
                await self._db.commit()
            except Exception:
                pass

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Helpers ──────────────────────────────────────────────────────────

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self._db.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self._db.execute(sql, params) as cur:
            return await cur.fetchall()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """Serialised write — returns lastrowid."""
        async with self._lock:
            cur = await self._db.execute(sql, params)
            await self._db.commit()
            return cur.lastrowid

    def _now_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Admins ──────────────────────────────────────────────────────────

    async def add_admin(self, discord_id: int, added_by: int) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO admins (discord_id, added_by) VALUES (?,?)",
            (discord_id, added_by),
        )
        await self.log_action("admin_added", actor_id=added_by, target_id=discord_id)

    async def remove_admin(self, discord_id: int, removed_by: int) -> bool:
        async with self._lock:
            cur = await self._db.execute(
                "DELETE FROM admins WHERE discord_id = ?", (discord_id,)
            )
            await self._db.commit()
        if cur.rowcount:
            await self.log_action("admin_removed", actor_id=removed_by, target_id=discord_id)
            return True
        return False

    async def is_admin(self, discord_id: int) -> bool:
        row = await self.fetchone("SELECT 1 FROM admins WHERE discord_id = ?", (discord_id,))
        return row is not None

    async def list_admins(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM admins ORDER BY added_at")

    # ── Log Channel (MISSING METHOD #1-2) ───────────────────────────────────

    async def set_log_channel(self, channel_id: int) -> None:
        """Set the Discord channel ID for bot action logs."""
        await self.execute(
            "INSERT OR REPLACE INTO log_channel (id, channel_id) VALUES (1, ?)",
            (channel_id,),
        )
        log.info("Set log channel to %d", channel_id)

    async def get_log_channel(self) -> int | None:
        """Get the Discord channel ID for bot action logs."""
        row = await self.fetchone("SELECT channel_id FROM log_channel WHERE id = 1")
        return row["channel_id"] if row else None

    # ── Maintenance Channels (MISSING METHOD #3-4) ───────────────────────────

    async def set_maintenance_channel(self, channel_id: int) -> None:
        """Add a channel to receive maintenance alerts."""
        await self.execute(
            "INSERT OR IGNORE INTO maintenance_channels (channel_id) VALUES (?)",
            (channel_id,),
        )
        log.info("Added maintenance channel %d", channel_id)

    async def get_maintenance_channels(self) -> list[aiosqlite.Row]:
        """Get all maintenance alert channels."""
        return await self.fetchall("SELECT channel_id FROM maintenance_channels")

    # ── Node Status Messages (MISSING METHODS #5-7) ──────────────────────────

    async def get_node_status_msg(
        self, node_id: int, channel_id: int
    ) -> aiosqlite.Row | None:
        """Get stored node status message ID for a node+channel pair."""
        return await self.fetchone(
            "SELECT * FROM node_status_messages WHERE node_id = ? AND channel_id = ?",
            (node_id, channel_id),
        )

    async def upsert_node_status_msg(
        self, node_id: int, channel_id: int, message_id: int
    ) -> None:
        """Create or update node status message record."""
        await self.execute(
            """INSERT OR REPLACE INTO node_status_messages
               (node_id, channel_id, message_id)
               VALUES (?,?,?)""",
            (node_id, channel_id, message_id),
        )

    async def get_node_status_msgs(self) -> list[aiosqlite.Row]:
        """Get all stored node status messages."""
        return await self.fetchall("SELECT * FROM node_status_messages")

    # ── Node Uptime Messages (MISSING METHODS #8-9) ───────────────────────────

    async def get_uptime_msgs(self) -> list[aiosqlite.Row]:
        """Get all stored uptime overview messages."""
        return await self.fetchall("SELECT * FROM node_uptime_messages")

    async def upsert_uptime_msg(self, channel_id: int, message_id: int) -> None:
        """Create or update uptime message record."""
        await self.execute(
            """INSERT OR REPLACE INTO node_uptime_messages
               (channel_id, message_id)
               VALUES (?,?)""",
            (channel_id, message_id),
        )

    # ── Maintenance Messages (MISSING METHODS #10-12) ───────────────────────

    async def upsert_maintenance_msg(
        self, node_id: int, channel_id: int, message_id: int
    ) -> None:
        """Create or update maintenance alert message record."""
        await self.execute(
            """INSERT OR REPLACE INTO maintenance_messages
               (node_id, channel_id, message_id)
               VALUES (?,?,?)""",
            (node_id, channel_id, message_id),
        )

    async def get_maintenance_msg(self, node_id: int) -> aiosqlite.Row | None:
        """Get stored maintenance message for a node."""
        return await self.fetchone(
            "SELECT * FROM maintenance_messages WHERE node_id = ?",
            (node_id,),
        )

    async def delete_maintenance_msg(self, node_id: int) -> None:
        """Delete maintenance message record for a node."""
        await self.execute(
            "DELETE FROM maintenance_messages WHERE node_id = ?",
            (node_id,),
        )

    # ── Free Hosting Stock Management ────────────────────────────────────

    async def create_stock_plan(
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
    ) -> None:
        """Create or update a free hosting plan."""
        import json
        
        await self.execute(
            """INSERT OR REPLACE INTO free_hosting_stock
               (plan_key, plan_label, description, cpu_percent, memory_mb, disk_mb,
                egg_ids, node_ids, total_stock, available_count)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                plan_key,
                plan_label,
                description,
                cpu_percent,
                memory_mb,
                disk_mb,
                json.dumps(egg_ids),
                json.dumps(node_ids),
                total_stock,
                total_stock,
            ),
        )
        log.info("Created/updated stock plan: %s (%d slots)", plan_key, total_stock)

    async def get_stock_plan(self, plan_key: str) -> aiosqlite.Row | None:
        """Fetch a single stock plan."""
        return await self.fetchone(
            "SELECT * FROM free_hosting_stock WHERE plan_key = ?",
            (plan_key,),
        )

    async def get_all_stock_plans(self, enabled_only: bool = False) -> list[aiosqlite.Row]:
        """List all stock plans."""
        if enabled_only:
            return await self.fetchall(
                "SELECT * FROM free_hosting_stock WHERE enabled = 1 ORDER BY plan_key"
            )
        return await self.fetchall("SELECT * FROM free_hosting_stock ORDER BY plan_key")

    async def update_stock_availability(self, plan_key: str, claimed_count: int) -> None:
        """Update claimed count and recalculate available."""
        row = await self.get_stock_plan(plan_key)
        if not row:
            return
        
        total = row["total_stock"]
        available = max(0, total - claimed_count)
        
        await self.execute(
            """UPDATE free_hosting_stock
               SET claimed_count = ?, available_count = ?, updated_at = ?
               WHERE plan_key = ?""",
            (claimed_count, available, self._now_utc(), plan_key),
        )

    async def enable_stock_plan(self, plan_key: str, admin_id: int) -> None:
        """Enable a stock plan."""
        await self.execute(
            "UPDATE free_hosting_stock SET enabled = 1, updated_at = ? WHERE plan_key = ?",
            (self._now_utc(), plan_key),
        )
        await self.log_action(
            "stock_enabled",
            actor_id=admin_id,
            details=f"plan_key={plan_key}",
        )

    async def disable_stock_plan(self, plan_key: str, admin_id: int, reason: str = "") -> None:
        """Disable a stock plan."""
        await self.execute(
            "UPDATE free_hosting_stock SET enabled = 0, updated_at = ? WHERE plan_key = ?",
            (self._now_utc(), plan_key),
        )
        await self.log_action(
            "stock_disabled",
            actor_id=admin_id,
            details=f"plan_key={plan_key} reason={reason}",
        )

    # ── Free Hosting Claims ──────────────────────────────────────────────

    async def create_claim(
        self,
        discord_id: int,
        plan_key: str,
    ) -> int | None:
        """
        Create a new free hosting claim (one per user per plan).
        
        Returns:
            Claim ID on success, None if user already claimed this plan.
        """
        # Check for existing claim
        existing = await self.fetchone(
            "SELECT id FROM free_hosting_claims WHERE discord_id = ? AND plan_key = ?",
            (discord_id, plan_key),
        )
        if existing:
            return None
        
        claim_id = await self.execute(
            """INSERT INTO free_hosting_claims
               (discord_id, plan_key, status)
               VALUES (?,?,'pending')""",
            (discord_id, plan_key),
        )
        
        log.info(
            "Created claim %d for user %d on plan %s",
            claim_id,
            discord_id,
            plan_key,
        )
        return claim_id

    async def get_claim(self, claim_id: int) -> aiosqlite.Row | None:
        """Fetch a single claim by ID."""
        return await self.fetchone(
            "SELECT * FROM free_hosting_claims WHERE id = ?",
            (claim_id,),
        )

    async def get_user_claim(
        self,
        discord_id: int,
        plan_key: str,
    ) -> aiosqlite.Row | None:
        """Fetch a user's claim for a specific plan."""
        return await self.fetchone(
            "SELECT * FROM free_hosting_claims WHERE discord_id = ? AND plan_key = ?",
            (discord_id, plan_key),
        )

    async def get_user_claims(self, discord_id: int) -> list[aiosqlite.Row]:
        """Fetch all claims by a user."""
        return await self.fetchall(
            "SELECT * FROM free_hosting_claims WHERE discord_id = ? ORDER BY claimed_at DESC",
            (discord_id,),
        )

    async def get_claims_by_status(self, status: str) -> list[aiosqlite.Row]:
        """Fetch all claims with a specific status."""
        return await self.fetchall(
            "SELECT * FROM free_hosting_claims WHERE status = ? ORDER BY claimed_at DESC",
            (status,),
        )

    async def approve_claim(
        self,
        claim_id: int,
        approved_by: int,
        notes: str = "",
    ) -> bool:
        """Approve a pending claim."""
        claim = await self.get_claim(claim_id)
        if not claim or claim["status"] != "pending":
            return False
        
        await self.execute(
            """UPDATE free_hosting_claims
               SET status = 'approved', approved_at = ?, approved_by = ?, notes = ?
               WHERE id = ?""",
            (self._now_utc(), approved_by, notes, claim_id),
        )
        
        await self.log_action(
            "claim_approved",
            actor_id=approved_by,
            target_id=claim["discord_id"],
            details=f"claim_id={claim_id} plan_key={claim['plan_key']}",
        )
        return True

    async def reject_claim(
        self,
        claim_id: int,
        rejected_by: int,
        reason: str = "No reason provided",
    ) -> bool:
        """Reject a pending claim."""
        claim = await self.get_claim(claim_id)
        if not claim or claim["status"] != "pending":
            return False
        
        await self.execute(
            """UPDATE free_hosting_claims
               SET status = 'rejected', rejection_reason = ?
               WHERE id = ?""",
            (reason, claim_id),
        )
        
        await self.log_action(
            "claim_rejected",
            actor_id=rejected_by,
            target_id=claim["discord_id"],
            details=f"claim_id={claim_id} reason={reason}",
        )
        return True

    async def revoke_claim(
        self,
        claim_id: int,
        revoked_by: int,
        reason: str = "No reason provided",
    ) -> bool:
        """Revoke an approved or provisioned claim."""
        claim = await self.get_claim(claim_id)
        if not claim:
            return False
        
        await self.execute(
            """UPDATE free_hosting_claims
               SET status = 'revoked', revoked_at = ?, revoked_by = ?, notes = ?
               WHERE id = ?""",
            (self._now_utc(), revoked_by, reason, claim_id),
        )
        
        await self.log_action(
            "claim_revoked",
            actor_id=revoked_by,
            target_id=claim["discord_id"],
            details=f"claim_id={claim_id} reason={reason}",
        )
        return True

    async def mark_claim_provisioned(
        self,
        claim_id: int,
        pterodactyl_user_id: int,
        server_id: int,
        provisioned_by: int,
    ) -> bool:
        """Mark a claim as provisioned after server creation."""
        await self.execute(
            """UPDATE free_hosting_claims
               SET status = 'provisioned', pterodactyl_user_id = ?, server_id = ?,
                   provisioned_at = ?, provisioned_by = ?
               WHERE id = ?""",
            (pterodactyl_user_id, server_id, self._now_utc(), provisioned_by, claim_id),
        )
        return True

    # ── Claim Validation Logs ────────────────────────────────────────────

    async def log_validation(
        self,
        claim_id: int,
        discord_id: int,
        plan_key: str,
        validation_type: str,
        status: str,
        details: str = "",
        validated_by: int | None = None,
    ) -> None:
        """Log a validation step."""
        await self.execute(
            """INSERT INTO claim_validation_logs
               (claim_id, discord_id, plan_key, validation_type, status, details, validated_by)
               VALUES (?,?,?,?,?,?,?)""",
            (claim_id, discord_id, plan_key, validation_type, status, details, validated_by),
        )

    async def get_validation_logs(self, claim_id: int) -> list[aiosqlite.Row]:
        """Get validation history for a claim."""
        return await self.fetchall(
            "SELECT * FROM claim_validation_logs WHERE claim_id = ? ORDER BY validated_at DESC",
            (claim_id,),
        )

    # ── Provisioned Servers ──────────────────────────────────────────────

    async def record_provisioned_server(
        self,
        claim_id: int,
        discord_id: int,
        plan_key: str,
        pterodactyl_server_id: int,
        server_name: str,
        provisioned_by: int,
    ) -> int:
        """Record a server that was auto-provisioned for a claim."""
        return await self.execute(
            """INSERT INTO provisioned_servers
               (claim_id, discord_id, plan_key, pterodactyl_server_id, server_name, provisioned_by)
               VALUES (?,?,?,?,?,?)""",
            (claim_id, discord_id, plan_key, pterodactyl_server_id, server_name, provisioned_by),
        )

    async def get_provisioned_server(self, claim_id: int) -> aiosqlite.Row | None:
        """Fetch provisioned server record for a claim."""
        return await self.fetchone(
            "SELECT * FROM provisioned_servers WHERE claim_id = ?",
            (claim_id,),
        )

    async def mark_credentials_issued(self, claim_id: int) -> None:
        """Mark that credentials have been issued to the user."""
        await self.execute(
            "UPDATE provisioned_servers SET credentials_issued = 1 WHERE claim_id = ?",
            (claim_id,),
        )

    # ── Action log ─────────────────────────────────────────────────────────

    async def log_action(
        self,
        action: str,
        actor_id: int | None = None,
        target_id: int | None = None,
        details: str | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO action_logs (action, actor_id, target_id, details) VALUES (?,?,?,?)",
            (action, actor_id, target_id, details),
        )

    async def get_recent_logs(self, limit: int = 50) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM action_logs ORDER BY id DESC LIMIT ?", (limit,)
        )

    # ── Node uptime (keep existing methods) ───────────────────────────────

    async def ensure_node_uptime_row(self, node_id: int, node_name: str) -> None:
        await self.execute(
            """INSERT OR IGNORE INTO node_uptime (node_id, node_name)
               VALUES (?,?)""",
            (node_id, node_name),
        )

    async def get_node_uptime(self, node_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM node_uptime WHERE node_id = ?", (node_id,)
        )

    async def get_all_node_uptimes(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM node_uptime")

    async def mark_node_online(self, node_id: int, node_name: str) -> None:
        now = self._now_utc()
        row = await self.get_node_uptime(node_id)
        if row is None:
            await self.ensure_node_uptime_row(node_id, node_name)
            row = await self.get_node_uptime(node_id)

        last_downtime_s = 0
        if row["offline_since"]:
            try:
                from datetime import datetime
                offline_dt = datetime.fromisoformat(
                    row["offline_since"].replace("Z", "+00:00")
                )
                last_downtime_s = int(
                    (datetime.now(timezone.utc) - offline_dt).total_seconds()
                )
            except Exception:
                pass

        new_month = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00Z")
        month_downtime = row["month_downtime_s"] if row["month_start"] == new_month else 0

        await self.execute(
            """UPDATE node_uptime SET
                node_name=?,
                online_since=?,
                offline_since=NULL,
                last_downtime_s=?,
                total_downtime_s=total_downtime_s + ?,
                month_start=?,
                month_downtime_s=? + ?,
                last_seen_online=?,
                updated_at=?
            WHERE node_id=?""",
            (
                node_name,
                now,
                last_downtime_s,
                last_downtime_s,
                new_month,
                month_downtime,
                last_downtime_s,
                now,
                now,
                node_id,
            ),
        )
        await self._add_node_event(node_id, node_name, "online")

    async def mark_node_offline(self, node_id: int, node_name: str) -> None:
        now = self._now_utc()
        row = await self.get_node_uptime(node_id)
        if row is None:
            await self.ensure_node_uptime_row(node_id, node_name)
            row = await self.get_node_uptime(node_id)

        session_s = 0
        if row["online_since"]:
            try:
                from datetime import datetime
                online_dt = datetime.fromisoformat(
                    row["online_since"].replace("Z", "+00:00")
                )
                session_s = int(
                    (datetime.now(timezone.utc) - online_dt).total_seconds()
                )
            except Exception:
                pass

        await self.execute(
            """UPDATE node_uptime SET
                node_name=?,
                offline_since=?,
                online_since=NULL,
                last_online_session_s=?,
                updated_at=?
            WHERE node_id=?""",
            (node_name, now, session_s, now, node_id),
        )
        await self._add_node_event(node_id, node_name, "offline")

    async def touch_node_online(self, node_id: int, node_name: str) -> None:
        now = self._now_utc()
        row = await self.get_node_uptime(node_id)
        if row is None:
            await self.ensure_node_uptime_row(node_id, node_name)
            await self.execute(
                "UPDATE node_uptime SET online_since=?, last_seen_online=?, updated_at=? WHERE node_id=?",
                (now, now, now, node_id),
            )
            return
        await self.execute(
            "UPDATE node_uptime SET last_seen_online=?, updated_at=? WHERE node_id=?",
            (now, now, node_id),
        )

    async def get_monthly_uptime_pct(self, node_id: int) -> float:
        row = await self.get_node_uptime(node_id)
        if not row:
            return 100.0
        now = datetime.now(timezone.utc)
        new_month = now.strftime("%Y-%m-01T00:00:00Z")
        if row["month_start"] != new_month:
            return 100.0
        try:
            from datetime import datetime as dt
            month_start_dt = dt.fromisoformat(row["month_start"].replace("Z", "+00:00"))
            elapsed_s = (now - month_start_dt).total_seconds()
            if elapsed_s <= 0:
                return 100.0
            downtime = row["month_downtime_s"] or 0
            if row["offline_since"]:
                try:
                    offline_dt = dt.fromisoformat(
                        row["offline_since"].replace("Z", "+00:00")
                    )
                    downtime += int((now - offline_dt).total_seconds())
                except Exception:
                    pass
            pct = max(0.0, min(100.0, (1.0 - downtime / elapsed_s) * 100.0))
            return round(pct, 2)
        except Exception:
            return 100.0

    async def _add_node_event(
        self, node_id: int, node_name: str, event: str
    ) -> None:
        await self.execute(
            "INSERT INTO node_events (node_id, node_name, event) VALUES (?,?,?)",
            (node_id, node_name, event),
        )

    async def open_downtime(self, node_id: int, node_name: str) -> int:
        return await self.execute(
            """INSERT INTO node_downtime_log (node_id, node_name, down_at)
               VALUES (?,?,?)""",
            (node_id, node_name, self._now_utc()),
        )

    async def close_downtime(self, node_id: int) -> int | None:
        row = await self.fetchone(
            """SELECT id, down_at FROM node_downtime_log
               WHERE node_id=? AND up_at IS NULL ORDER BY id DESC LIMIT 1""",
            (node_id,),
        )
        if not row:
            return None
        from datetime import datetime
        down_at = datetime.fromisoformat(row["down_at"].replace("Z", "+00:00"))
        up_at = datetime.now(timezone.utc)
        duration = int((up_at - down_at).total_seconds())
        await self.execute(
            "UPDATE node_downtime_log SET up_at=?, duration_s=? WHERE id=?",
            (up_at.strftime("%Y-%m-%dT%H:%M:%SZ"), duration, row["id"]),
        )
        return duration

    async def get_open_downtimes(self) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM node_downtime_log WHERE up_at IS NULL"
        )
