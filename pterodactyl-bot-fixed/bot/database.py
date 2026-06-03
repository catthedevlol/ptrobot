"""
database.py — Async database layer (aiosqlite).

Tables
------
admins                — sub-admin Discord IDs
pterodactyl_users     — Discord → Pterodactyl user mapping
pterodactyl_servers   — Discord user → Pterodactyl server mapping
node_status_messages  — persistent status embed tracking
node_uptime_messages  — persistent uptime embed tracking
maintenance_messages  — persistent maintenance embed tracking
maintenance_channels  — channels to receive maintenance alerts
node_downtime_log     — raw downtime events (open/close)
node_uptime           — per-node uptime statistics (survives restarts)
node_events           — ordered timeline of online/offline events
node_history          — monthly uptime snapshots
server_logs           — per-server audit events
action_logs           — global audit log entries
log_channel           — configured log channel
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

log = logging.getLogger("bot.database")


# ─────────────────────────────────────────────────────────────────────────────
# DDL — all tables created with IF NOT EXISTS so migrations are safe
# ─────────────────────────────────────────────────────────────────────────────
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

-- Raw downtime events (one row per outage)
CREATE TABLE IF NOT EXISTS node_downtime_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    node_name   TEXT    NOT NULL,
    down_at     TEXT    NOT NULL,   -- ISO-8601 UTC
    up_at       TEXT,               -- NULL while still down
    duration_s  INTEGER             -- filled when up_at is set
);

-- Per-node cumulative uptime stats — survives bot restarts
CREATE TABLE IF NOT EXISTS node_uptime (
    node_id                 INTEGER PRIMARY KEY,
    node_name               TEXT    NOT NULL DEFAULT '',
    online_since            TEXT,   -- UTC ISO-8601, set when node came online
    offline_since           TEXT,   -- UTC ISO-8601, set when node went offline
    last_online_session_s   INTEGER NOT NULL DEFAULT 0,
    last_downtime_s         INTEGER NOT NULL DEFAULT 0,
    total_downtime_s        INTEGER NOT NULL DEFAULT 0,
    month_start             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-01T00:00:00Z','now')),
    month_downtime_s        INTEGER NOT NULL DEFAULT 0,
    last_seen_online        TEXT,   -- last time we successfully polled the node
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Timeline of state-change events for each node
CREATE TABLE IF NOT EXISTS node_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    node_name   TEXT    NOT NULL,
    event       TEXT    NOT NULL CHECK (event IN ('online','offline')),
    ts          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Monthly uptime snapshots
CREATE TABLE IF NOT EXISTS node_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER NOT NULL,
    node_name       TEXT    NOT NULL,
    month           TEXT    NOT NULL,   -- YYYY-MM
    uptime_pct      REAL    NOT NULL,
    total_downtime_s INTEGER NOT NULL,
    recorded_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (node_id, month)
);

-- Per-server event log
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
"""

# Migration: add columns that might be missing in existing databases
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
        self._lock = asyncio.Lock()  # serialise writes to prevent locking issues

    async def init(self) -> None:
        """Open the connection, apply schema, run migrations."""
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        # WAL mode: concurrent reads while writing, better crash recovery
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._run_migrations()
        log.info("Database initialised at %s", self._path)

    async def _run_migrations(self) -> None:
        """Apply any ALTER TABLE migrations idempotently (ignore 'already exists' errors)."""
        for stmt in MIGRATIONS:
            try:
                await self._db.execute(stmt)
                await self._db.commit()
            except Exception:
                pass  # column already exists — safe to ignore

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

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

    # ── Admins ────────────────────────────────────────────────────────────────

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

    # ── Pterodactyl users ─────────────────────────────────────────────────────

    async def create_ptero_user(
        self,
        discord_id: int,
        ptero_id: int,
        username: str,
        email: str,
        password: str,
        created_by: int,
    ) -> None:
        await self.execute(
            """INSERT OR REPLACE INTO pterodactyl_users
               (discord_id, ptero_id, username, email, password, created_by)
               VALUES (?,?,?,?,?,?)""",
            (discord_id, ptero_id, username, email, password, created_by),
        )
        await self.log_action(
            "user_created",
            actor_id=created_by,
            target_id=discord_id,
            details=f"ptero_id={ptero_id} username={username}",
        )

    async def get_ptero_user(self, discord_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM pterodactyl_users WHERE discord_id = ?", (discord_id,)
        )

    async def username_exists(self, username: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM pterodactyl_users WHERE username = ?", (username,)
        )
        return row is not None

    # ── Pterodactyl servers ───────────────────────────────────────────────────

    async def create_ptero_server(
        self,
        discord_id: int,
        ptero_server_id: int,
        identifier: str,
        name: str,
        egg_id: int,
        node_id: int,
        created_by: int,
        *,
        node_name: str = "",
        egg_name: str = "",
        software: str = "",
        plan_label: str = "",
        cpu: int = 0,
        ram: int = 0,
        disk: int = 0,
        allocation_ip: str = "",
        allocation_port: int = 0,
    ) -> int:
        row_id = await self.execute(
            """INSERT INTO pterodactyl_servers
               (discord_id, ptero_server_id, identifier, name, egg_id, node_id,
                node_name, egg_name, software, plan_label, cpu, ram, disk,
                allocation_ip, allocation_port, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (discord_id, ptero_server_id, identifier, name, egg_id, node_id,
             node_name, egg_name, software, plan_label, cpu, ram, disk,
             allocation_ip, allocation_port, created_by),
        )
        await self.log_action(
            "server_created",
            actor_id=created_by,
            target_id=discord_id,
            details=f"server_id={ptero_server_id} name={name}",
        )
        await self.log_server_event(ptero_server_id, discord_id, "created", f"name={name}")
        return row_id

    async def get_servers_by_discord(self, discord_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM pterodactyl_servers WHERE discord_id = ? ORDER BY created_at",
            (discord_id,),
        )

    async def delete_ptero_server(self, ptero_server_id: int, deleted_by: int) -> None:
        row = await self.fetchone(
            "SELECT * FROM pterodactyl_servers WHERE ptero_server_id = ?",
            (ptero_server_id,),
        )
        if row:
            await self.execute(
                "DELETE FROM pterodactyl_servers WHERE ptero_server_id = ?",
                (ptero_server_id,),
            )
            await self.log_action(
                "server_deleted",
                actor_id=deleted_by,
                target_id=row["discord_id"],
                details=f"server_id={ptero_server_id} name={row['name']}",
            )
            await self.log_server_event(
                ptero_server_id, row["discord_id"], "deleted",
                f"deleted_by={deleted_by}"
            )

    # ── Server logs ───────────────────────────────────────────────────────────

    async def log_server_event(
        self,
        server_id: int,
        discord_id: int,
        event: str,
        details: str | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO server_logs (server_id, discord_id, event, details) VALUES (?,?,?,?)",
            (server_id, discord_id, event, details),
        )

    # ── Node status messages ──────────────────────────────────────────────────

    async def upsert_node_status_msg(
        self, node_id: int, channel_id: int, message_id: int
    ) -> None:
        await self.execute(
            """INSERT OR REPLACE INTO node_status_messages (node_id, channel_id, message_id)
               VALUES (?,?,?)""",
            (node_id, channel_id, message_id),
        )

    async def get_node_status_msgs(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM node_status_messages")

    async def get_node_status_msg(
        self, node_id: int, channel_id: int
    ) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM node_status_messages WHERE node_id=? AND channel_id=?",
            (node_id, channel_id),
        )

    # ── Node uptime messages ──────────────────────────────────────────────────

    async def upsert_uptime_msg(self, channel_id: int, message_id: int) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO node_uptime_messages (channel_id, message_id) VALUES (?,?)",
            (channel_id, message_id),
        )

    async def get_uptime_msgs(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM node_uptime_messages")

    # ── Maintenance messages ──────────────────────────────────────────────────

    async def upsert_maintenance_msg(
        self, node_id: int, channel_id: int, message_id: int
    ) -> None:
        await self.execute(
            """INSERT OR REPLACE INTO maintenance_messages (node_id, channel_id, message_id)
               VALUES (?,?,?)""",
            (node_id, channel_id, message_id),
        )

    async def get_maintenance_msg(self, node_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM maintenance_messages WHERE node_id = ?", (node_id,)
        )

    async def delete_maintenance_msg(self, node_id: int) -> None:
        await self.execute(
            "DELETE FROM maintenance_messages WHERE node_id = ?", (node_id,)
        )

    async def set_maintenance_channel(self, channel_id: int) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO maintenance_channels (channel_id) VALUES (?)",
            (channel_id,),
        )

    async def get_maintenance_channels(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM maintenance_channels")

    # ── Raw downtime log ──────────────────────────────────────────────────────

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

    # ── Advanced node uptime stats ────────────────────────────────────────────

    async def ensure_node_uptime_row(self, node_id: int, node_name: str) -> None:
        """Create the uptime row for a node if it doesn't exist."""
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
        """Record a node coming online — sets online_since, clears offline_since."""
        now = self._now_utc()
        row = await self.get_node_uptime(node_id)
        if row is None:
            await self.ensure_node_uptime_row(node_id, node_name)
            row = await self.get_node_uptime(node_id)

        # Calculate how long it was offline
        last_downtime_s = 0
        if row["offline_since"]:
            try:
                offline_dt = datetime.fromisoformat(
                    row["offline_since"].replace("Z", "+00:00")
                )
                last_downtime_s = int(
                    (datetime.now(timezone.utc) - offline_dt).total_seconds()
                )
            except Exception:
                pass

        # Reset month bucket if we're in a new month
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
                month_downtime, last_downtime_s,
                now,
                now,
                node_id,
            ),
        )
        await self._add_node_event(node_id, node_name, "online")

    async def mark_node_offline(self, node_id: int, node_name: str) -> None:
        """Record a node going offline — sets offline_since, records online session duration."""
        now = self._now_utc()
        row = await self.get_node_uptime(node_id)
        if row is None:
            await self.ensure_node_uptime_row(node_id, node_name)
            row = await self.get_node_uptime(node_id)

        # Calculate how long the online session lasted
        session_s = 0
        if row["online_since"]:
            try:
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
        """Update last_seen_online without changing state (called every poll when node is up)."""
        now = self._now_utc()
        row = await self.get_node_uptime(node_id)
        if row is None:
            await self.ensure_node_uptime_row(node_id, node_name)
            # Node just came to our attention — treat as first online
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
        """Return this month's uptime % for the node."""
        row = await self.get_node_uptime(node_id)
        if not row:
            return 100.0
        now = datetime.now(timezone.utc)
        new_month = now.strftime("%Y-%m-01T00:00:00Z")
        if row["month_start"] != new_month:
            return 100.0  # no data for this month yet
        try:
            month_start_dt = datetime.fromisoformat(row["month_start"].replace("Z", "+00:00"))
            elapsed_s = (now - month_start_dt).total_seconds()
            if elapsed_s <= 0:
                return 100.0
            downtime = row["month_downtime_s"] or 0
            # Add current ongoing downtime if node is offline right now
            if row["offline_since"]:
                try:
                    offline_dt = datetime.fromisoformat(
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

    # ── Log channel ───────────────────────────────────────────────────────────

    async def set_log_channel(self, channel_id: int) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO log_channel (id, channel_id) VALUES (1,?)",
            (channel_id,),
        )

    async def get_log_channel(self) -> int | None:
        row = await self.fetchone("SELECT channel_id FROM log_channel WHERE id=1")
        return row["channel_id"] if row else None

    # ── Action log ────────────────────────────────────────────────────────────

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
