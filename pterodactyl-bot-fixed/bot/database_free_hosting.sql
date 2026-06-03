-- Free hosting tier system schema additions
-- Run this file to add free hosting support: sqlite3 data/bot.db < database_free_hosting.sql

-- Free hosting plans (admin-created templates)
CREATE TABLE IF NOT EXISTS free_hosting_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT    NOT NULL UNIQUE,
    cpu_pct         INTEGER NOT NULL,
    ram_mb          INTEGER NOT NULL,
    disk_mb         INTEGER NOT NULL,
    node_id         INTEGER NOT NULL,
    egg_id          INTEGER NOT NULL,
    nest_id         INTEGER NOT NULL DEFAULT 1,
    total_stock     INTEGER NOT NULL,
    available_stock INTEGER NOT NULL,
    created_by      INTEGER NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Track which Discord users have claimed free servers (one per user)
CREATE TABLE IF NOT EXISTS free_hosting_claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      INTEGER NOT NULL UNIQUE,
    plan_id         INTEGER NOT NULL,
    server_id       INTEGER NOT NULL,
    claimed_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    FOREIGN KEY (plan_id) REFERENCES free_hosting_plans(id)
);

-- Node uptime persistent tracking (real statistics, not fake)
CREATE TABLE IF NOT EXISTS node_uptime (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id             INTEGER NOT NULL UNIQUE,
    online_since        TEXT,
    offline_since       TEXT,
    total_uptime_ms     INTEGER NOT NULL DEFAULT 0,
    total_downtime_ms   INTEGER NOT NULL DEFAULT 0,
    current_session_ms  INTEGER NOT NULL DEFAULT 0,
    last_updated        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Node downtime event log (for audit and statistics)
CREATE TABLE IF NOT EXISTS node_downtime_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    recovered_at TEXT,
    duration_ms INTEGER,
    reason      TEXT
);

-- Node state change events (audit trail)
CREATE TABLE IF NOT EXISTS node_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    old_state   TEXT,
    new_state   TEXT,
    timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Monthly uptime snapshots for statistics
CREATE TABLE IF NOT EXISTS node_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    uptime_pct  REAL NOT NULL,
    downtime_ms INTEGER NOT NULL,
    snapshot_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (node_id, year, month)
);
